"""Threaded worker owning the NML_Hand_Exo dual-USB-CDC link.

The worker is the single owner of the serial transport and the
``nml_hand_exo.HandExo`` instance built on top of it.  Every device
interaction runs on one dedicated thread drained from a command queue, so the
async service and any GUI never touch the serial port directly -- the same
ownership discipline :class:`~stateful_decode_and_sync.controller.BroadbandController`
uses for the Synapse Taps.

Scope is the *position* control model of the exo's ``08_udp`` example
(``examples/08_udp``): a connect/arm/home/disarm lifecycle, a batched
``set_finger_angles`` write per commanded pose (signed ``[-100, 100]`` per
joint, rest-anchored), periodic pose read-back, and an inactivity watchdog that
eases the hand back to a neutral rest pose when commands stop arriving.  Torque
/ direct-current control is deliberately out of scope for now.

The worker depends on the ``nml_hand_exo`` SDK, which is an optional install of
this client (``pip install -e '.[exo]'`` wires it to the ``third_party/exo``
submodule).  It is imported lazily so the rest of the client, its tests, and the
neural-device service import cleanly without the SDK present.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

#: Firmware required by ``set_finger_angles`` (see the SDK's FW_SET_FINGER_ANGLES).
#: Duplicated here so the worker can gate before importing the SDK constant, and
#: verified against it in :meth:`ExoWorker._verify_firmware`.
MIN_FINGER_ANGLES_FIRMWARE = (0, 6, 4)

#: Wire/joint order for the batch command and every pose snapshot.  Mirrors the
#: SDK's SET_FINGER_ANGLES_ORDER; asserted equal at import of the SDK.
JOINT_ORDER = ("thumb", "index", "middle", "ring", "pinky", "wrist")

#: Neutral rest value on the signed ``[-100, 100]`` finger axis.
REST_VALUE = 0


class ExoError(RuntimeError):
    """An exo command could not be carried out."""


class ExoNotConnectedError(ExoError):
    """A command needing the device arrived while the link was down."""


@dataclass(frozen=True)
class ExoConfig:
    """Static configuration for one exo session.

    ``comm_factory`` builds the transport the worker will own.  It exists so a
    test can inject an in-process fake and hardware runs can inject a real
    :class:`nml_hand_exo.DualSerialComm` without the worker importing either.
    """

    comm_factory: Callable[[], Any]
    baudrate: int = 1_000_000
    #: Combined current budget in mA applied at arm time (firmware >= 0.4.0).
    #: ``None`` leaves whatever the firmware booted with alone.
    total_current_ma: int | None = 800
    #: Per-motor nominal working current in mA applied at arm time.  ``None``
    #: leaves the firmware default alone.
    per_motor_current_ma: int | None = 250
    #: Seconds without a pose command after which the watchdog eases the hand
    #: back to :data:`REST_VALUE`.  ``None`` disables the watchdog.
    watchdog_s: float | None = 1.0
    #: Pose read-back cadence in seconds.  ``None`` disables polling.
    poll_interval_s: float | None = 0.2
    #: Per-command reply timeout in seconds.
    reply_timeout_s: float = 0.75


@dataclass(frozen=True)
class ExoState:
    """Immutable snapshot broadcast to subscribers after every change.

    Mirrors the replace-on-change discipline of ``AppState``: subscribers keep
    the last value they were handed and never mutate one in place.
    """

    version: int = 0
    connected: bool = False
    armed: bool = False
    firmware: str = ""
    firmware_ok: bool = False
    #: Last commanded signed value per joint (what the host asked for).
    commanded: dict[str, int] = field(default_factory=dict)
    #: Last read-back pose: joint -> {"fraction": int, "angle_delta_deg": float|None}.
    pose: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_command_monotonic_s: float | None = None
    watchdog_tripped: bool = False
    last_error: str | None = None


def state_to_json(state: ExoState) -> dict[str, Any]:
    """Render an :class:`ExoState` as the wire dictionary the service emits."""
    return {
        "type": "exo_state",
        "version": state.version,
        "connected": state.connected,
        "armed": state.armed,
        "firmware": state.firmware,
        "firmware_ok": state.firmware_ok,
        "commanded": dict(state.commanded),
        "pose": {
            joint: {
                "fraction": record.get("fraction"),
                "angle_delta_deg": record.get("angle_delta_deg"),
            }
            for joint, record in state.pose.items()
        },
        "last_command_monotonic_s": state.last_command_monotonic_s,
        "watchdog_tripped": state.watchdog_tripped,
        "last_error": state.last_error,
    }


@dataclass
class _Job:
    """One unit of work handed to the worker thread."""

    fn: Callable[[], Any]
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Exception | None = None


class ExoWorker:
    """Own the exo transport on one thread; accept jobs, publish state.

    Public methods enqueue a job and block for its result, so callers never see
    partial serial state; the internal worker thread is the only code that
    touches ``self._exo``.  The same thread also runs the pose poll and the
    inactivity watchdog between jobs.
    """

    def __init__(self, config: ExoConfig):
        self._config = config
        self._exo = None
        self._comm = None
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._state = ExoState()
        self._state_lock = threading.Lock()
        self._state_callbacks: list[Callable[[ExoState], None]] = []
        # Set by the worker thread once a pose has been commanded, cleared when
        # the watchdog eases the hand back so it fires at most once per idle gap.
        self._last_command_monotonic: float | None = None

    # -- lifecycle ----------------------------------------------------------

    @property
    def state(self) -> ExoState:
        with self._state_lock:
            return self._state

    def on_state(self, callback: Callable[[ExoState], None]) -> None:
        with self._state_lock:
            self._state_callbacks.append(callback)

    def start(self) -> None:
        """Spin up the worker thread.  Idempotent; does not open the port."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="exo-worker", daemon=True
            )
            self._thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Disarm, close the port, and stop the worker thread."""
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
        # Best-effort disarm/close on the worker thread while it still runs, so
        # the hand never stays energized after the process goes away.
        try:
            self._submit(self._do_disconnect, timeout=timeout)
        except Exception:
            pass
        self._stop.set()
        self._jobs.put(None)
        thread.join(timeout=timeout)
        with self._lifecycle_lock:
            self._thread = None

    # -- commands (enqueue + block) -----------------------------------------

    def connect(self, timeout: float = 10.0) -> ExoState:
        self.start()
        return self._submit(self._do_connect, timeout=timeout)

    def disconnect(self, timeout: float = 5.0) -> ExoState:
        return self._submit(self._do_disconnect, timeout=timeout)

    def arm(self, home: bool = True, timeout: float = 20.0) -> ExoState:
        return self._submit(lambda: self._do_arm(home=home), timeout=timeout)

    def disarm(self, timeout: float = 5.0) -> ExoState:
        return self._submit(self._do_disarm, timeout=timeout)

    def home(self, timeout: float = 10.0) -> ExoState:
        return self._submit(self._do_home, timeout=timeout)

    def set_finger_angles(
        self, values: dict[str, int | float | None], timeout: float = 5.0
    ) -> ExoState:
        """Command a batch pose.  ``values`` are signed ``[-100, 100]`` per joint."""
        prepared = self._prepare_finger_values(values)
        return self._submit(
            lambda: self._do_set_finger_angles(prepared), timeout=timeout
        )

    def read_pose(self, timeout: float = 5.0) -> ExoState:
        return self._submit(self._do_read_pose, timeout=timeout)

    def get_state(self) -> ExoState:
        return self.state

    # -- worker thread ------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self._drain_one_job()
            if job is not None:
                self._execute(job)
                continue
            # No job pending: run the between-command housekeeping (watchdog and
            # pose poll) then wait briefly for the next job.
            self._service_idle()

    def _drain_one_job(self) -> _Job | None:
        try:
            return self._jobs.get(timeout=self._idle_wait_s())
        except queue.Empty:
            return None

    def _idle_wait_s(self) -> float:
        candidates = [
            v
            for v in (self._config.poll_interval_s, self._config.watchdog_s)
            if v is not None
        ]
        return min(candidates) if candidates else 0.1

    def _execute(self, job: _Job) -> None:
        try:
            job.result = job.fn()
        except Exception as exc:  # noqa: BLE001 -- surfaced to the caller
            job.error = exc
            self._publish(last_error=str(exc))
        finally:
            job.done.set()

    def _service_idle(self) -> None:
        if self._exo is None:
            return
        now = time.monotonic()
        # Watchdog: ease to neutral once, after the configured idle gap.
        watchdog = self._config.watchdog_s
        if (
            watchdog is not None
            and self.state.armed
            and self._last_command_monotonic is not None
            and now - self._last_command_monotonic >= watchdog
            and not self.state.watchdog_tripped
        ):
            try:
                self._exo.set_finger_angles({j: REST_VALUE for j in JOINT_ORDER})
                self._publish(
                    commanded={j: REST_VALUE for j in JOINT_ORDER},
                    watchdog_tripped=True,
                )
            except Exception as exc:  # noqa: BLE001
                self._publish(last_error=f"watchdog neutral failed: {exc}")
        # Pose poll: keep the published pose fresh while connected.
        if self._config.poll_interval_s is not None and self.state.connected:
            try:
                self._refresh_pose()
            except Exception:
                # A dropped poll is not an error state; a failing command path
                # already publishes last_error.
                pass

    # -- device operations (worker thread only) -----------------------------

    def _do_connect(self) -> ExoState:
        if self._exo is not None:
            return self.state
        from nml_hand_exo import DualSerialComm, HandExo  # noqa: F401 (import check)
        from nml_hand_exo.interface._gesture_protocol import SET_FINGER_ANGLES_ORDER

        assert tuple(SET_FINGER_ANGLES_ORDER) == JOINT_ORDER, (
            "SDK finger order drifted from ExoWorker.JOINT_ORDER"
        )
        comm = self._config.comm_factory()
        comm.connect()
        exo = HandExo(comm, command_delimiter="\n")
        self._comm = comm
        self._exo = exo
        firmware, ok = self._verify_firmware()
        self._last_command_monotonic = None
        self._publish(
            connected=True,
            armed=False,
            firmware=firmware,
            firmware_ok=ok,
            commanded={},
            pose={},
            watchdog_tripped=False,
            last_command_monotonic_s=None,
            last_error=None,
        )
        return self.state

    def _do_disconnect(self) -> ExoState:
        if self._exo is not None:
            try:
                self._do_disarm()
            except Exception:
                pass
            try:
                self._exo.close()
            except Exception:
                pass
        self._exo = None
        self._comm = None
        self._last_command_monotonic = None
        self._publish(
            connected=False,
            armed=False,
            commanded={},
            pose={},
            watchdog_tripped=False,
            last_command_monotonic_s=None,
        )
        return self.state

    def _do_arm(self, home: bool) -> ExoState:
        exo = self._require_exo()
        if not self.state.firmware_ok:
            raise ExoError(
                "device firmware does not support set_finger_angles "
                f"(needs >= {'.'.join(map(str, MIN_FINGER_ANGLES_FIRMWARE))}); "
                f"reported {self.state.firmware or 'unknown'}"
            )
        # Current settings go out before torque so the motors never energize at
        # whatever budget the firmware booted with; the combined budget first,
        # since it constrains the per-motor value.
        if self._config.total_current_ma:
            exo.set_total_current_limit(self._config.total_current_ma)
        if self._config.per_motor_current_ma:
            exo.set_current_limit("all", self._config.per_motor_current_ma)
        exo.enable_motor("all")
        self._publish(armed=True, watchdog_tripped=False)
        if home:
            self._do_home()
        return self.state

    def _do_disarm(self) -> ExoState:
        exo = self._exo
        if exo is not None:
            exo.disable_motor("all")
        self._publish(armed=False)
        return self.state

    def _do_home(self) -> ExoState:
        exo = self._require_exo()
        exo.home("all")
        self._last_command_monotonic = time.monotonic()
        self._publish(
            commanded={j: REST_VALUE for j in JOINT_ORDER},
            watchdog_tripped=False,
            last_command_monotonic_s=self._last_command_monotonic,
        )
        return self.state

    def _do_set_finger_angles(self, values: dict[str, int]) -> ExoState:
        exo = self._require_exo()
        if not self.state.armed:
            raise ExoError("exo must be armed before commanding a pose")
        exo.set_finger_angles(values)
        self._last_command_monotonic = time.monotonic()
        commanded = dict(self.state.commanded)
        commanded.update(values)
        self._publish(
            commanded=commanded,
            watchdog_tripped=False,
            last_command_monotonic_s=self._last_command_monotonic,
        )
        return self.state

    def _do_read_pose(self) -> ExoState:
        self._require_exo()
        self._refresh_pose()
        return self.state

    def _refresh_pose(self) -> None:
        exo = self._exo
        if exo is None or not self.state.firmware_ok:
            return
        pose = exo.get_gesture_angles("all", timeout=self._config.reply_timeout_s)
        if pose:
            self._publish(pose=pose)

    # -- helpers ------------------------------------------------------------

    def _verify_firmware(self) -> tuple[str, bool]:
        exo = self._exo
        assert exo is not None
        try:
            version = exo.version()
        except Exception:
            version = ""
        try:
            ok = exo.firmware_at_least(MIN_FINGER_ANGLES_FIRMWARE)
        except Exception:
            ok = False
        return version, ok

    def _require_exo(self):
        if self._exo is None:
            raise ExoNotConnectedError("exo is not connected")
        return self._exo

    @staticmethod
    def _prepare_finger_values(values: dict[str, int | float | None]) -> dict[str, int]:
        """Validate joints and coerce to signed ints before the worker runs.

        Validation happens on the caller's thread so a malformed request is
        rejected synchronously without ever reaching the serial port.
        """
        if not isinstance(values, dict) or not values:
            raise ValueError("values must be a non-empty mapping of joint -> value")
        prepared: dict[str, int] = {}
        for joint, value in values.items():
            name = str(joint).strip().lower()
            if name not in JOINT_ORDER:
                raise ValueError(
                    f"unknown joint {joint!r}; expected one of {', '.join(JOINT_ORDER)}"
                )
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"value for {name!r} must be numeric, got {value!r}") from exc
            if not -100.0 <= number <= 100.0:
                raise ValueError(f"value for {name!r} must be in [-100, 100], got {number:g}")
            prepared[name] = int(round(number))
        if not prepared:
            raise ValueError("no joint carried a value; every joint was None")
        return prepared

    def _submit(self, fn: Callable[[], Any], *, timeout: float) -> Any:
        if self._thread is None or not self._thread.is_alive():
            raise ExoError("exo worker is not running")
        job = _Job(fn)
        self._jobs.put(job)
        if not job.done.wait(timeout):
            raise TimeoutError("exo command timed out")
        if job.error is not None:
            raise job.error
        return job.result

    def _publish(self, **changes: Any) -> None:
        with self._state_lock:
            self._state = replace(self._state, version=self._state.version + 1, **changes)
            value = self._state
            callbacks = tuple(self._state_callbacks)
        for callback in callbacks:
            try:
                callback(value)
            except Exception:
                pass
