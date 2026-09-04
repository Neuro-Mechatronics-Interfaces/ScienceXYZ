// Concrete RecordSink backed by the raw C libhdf5 API.  See hdf5_record_sink.hpp
// for the persisted layout.  This file carries none of the timeline/clock value
// logic (that lives in TaskRecorderHdf5Writer and is unit-tested against an
// in-memory FakeRecordSink); it only serializes the fully-formed value
// structures the writer hands it, following the loss-aware conventions the
// wireless HDF5 recorder already uses:
//
//   * a schema-versioned, self-describing file whose root attributes record
//     provenance verbatim;
//   * an append-only /events dataset so acknowledged start/stop/abort control
//     events (and their explicit discarded-sample counts) bound whatever was
//     persisted, even across a crash;
//   * whole-collection snapshot datasets (/transitions, /intervals,
//     /diagnostics, /clock_epochs) rewritten on every flush so a mid-recording
//     flush and a final stop flush leave identical, self-consistent state;
//   * a global flush after every write, so an interrupted recording still opens
//     as a readable file bounded by its control events.
//
// Every method reports success/failure; a libhdf5 error is surfaced (never
// swallowed) so partial-write loss is visible to the writer rather than hidden.

#include "hdf5_record_sink.hpp"

#include <hdf5.h>

#include <cstring>
#include <string>
#include <vector>

namespace app::recording {
namespace {

// A move-only owner for an HDF5 identifier.  HDF5 handles (files, datasets,
// dataspaces, datatypes, attributes, property lists) are all hid_t and must be
// closed with the matching H5*close call.  Closing on every error path is what
// keeps a long recording loop from leaking handles, so each raw resource is
// wrapped the moment it is created.
template <herr_t (*CloseFn)(hid_t)>
class Hid {
 public:
  Hid() = default;
  explicit Hid(hid_t id) : id_(id) {}
  ~Hid() { reset(); }

  Hid(const Hid&) = delete;
  Hid& operator=(const Hid&) = delete;
  Hid(Hid&& other) noexcept : id_(other.id_) { other.id_ = H5I_INVALID_HID; }
  Hid& operator=(Hid&& other) noexcept {
    if (this != &other) {
      reset();
      id_ = other.id_;
      other.id_ = H5I_INVALID_HID;
    }
    return *this;
  }

  hid_t get() const { return id_; }
  bool valid() const { return id_ >= 0; }
  explicit operator bool() const { return valid(); }

  hid_t release() {
    hid_t id = id_;
    id_ = H5I_INVALID_HID;
    return id;
  }
  void reset() {
    if (id_ >= 0) {
      CloseFn(id_);
      id_ = H5I_INVALID_HID;
    }
  }

 private:
  hid_t id_ = H5I_INVALID_HID;
};

using File = Hid<H5Fclose>;
using Group = Hid<H5Gclose>;
using DataSet = Hid<H5Dclose>;
using DataSpace = Hid<H5Sclose>;
using DataType = Hid<H5Tclose>;
using Attribute = Hid<H5Aclose>;
using PropList = Hid<H5Pclose>;

// A variable-length UTF-8 string type.  Compound members that carry text store a
// char* into this type; HDF5 owns neither our std::string storage nor the
// pointers we pass, so the backing strings must outlive the H5Dwrite call.
DataType make_vlen_string_type() {
  DataType type(H5Tcopy(H5T_C_S1));
  if (!type) return type;
  if (H5Tset_size(type.get(), H5T_VARIABLE) < 0) return DataType();
  // Tag the encoding so a reader treats the bytes as UTF-8 rather than ASCII.
  H5Tset_cset(type.get(), H5T_CSET_UTF8);
  return type;
}

// Write (create or overwrite) a scalar string attribute on an object.
bool write_string_attribute(hid_t object, const char* name,
                            const std::string& value) {
  if (H5Aexists(object, name) > 0) {
    H5Adelete(object, name);
  }
  DataType type = make_vlen_string_type();
  if (!type) return false;
  DataSpace space(H5Screate(H5S_SCALAR));
  if (!space) return false;
  Attribute attr(H5Acreate2(object, name, type.get(), space.get(), H5P_DEFAULT,
                            H5P_DEFAULT));
  if (!attr) return false;
  const char* ptr = value.c_str();
  return H5Awrite(attr.get(), type.get(), &ptr) >= 0;
}

bool write_u64_attribute(hid_t object, const char* name, std::uint64_t value) {
  if (H5Aexists(object, name) > 0) {
    H5Adelete(object, name);
  }
  DataSpace space(H5Screate(H5S_SCALAR));
  if (!space) return false;
  Attribute attr(H5Acreate2(object, name, H5T_STD_U64LE, space.get(),
                            H5P_DEFAULT, H5P_DEFAULT));
  if (!attr) return false;
  return H5Awrite(attr.get(), H5T_NATIVE_UINT64, &value) >= 0;
}

bool write_u8_attribute(hid_t object, const char* name, std::uint8_t value) {
  if (H5Aexists(object, name) > 0) {
    H5Adelete(object, name);
  }
  DataSpace space(H5Screate(H5S_SCALAR));
  if (!space) return false;
  Attribute attr(H5Acreate2(object, name, H5T_STD_U8LE, space.get(),
                            H5P_DEFAULT, H5P_DEFAULT));
  if (!attr) return false;
  return H5Awrite(attr.get(), H5T_NATIVE_UINT8, &value) >= 0;
}

// Compound-member helper: insert a field at the given offset.  Returns false so
// the caller can abort building a malformed type instead of writing garbage.
bool insert_member(hid_t type, const char* name, size_t offset,
                   hid_t member_type) {
  return H5Tinsert(type, name, offset, member_type) >= 0;
}

}  // namespace

// ---------------------------------------------------------------------------
// Flat POD rows.  Each timeline/clock value struct is copied into a packed row
// whose layout the compound HDF5 datatype describes exactly.  Strings are held
// alive in a side vector and referenced by char* so a reader gets real text,
// and no source field is dropped: every value-struct field maps to a column.
// ---------------------------------------------------------------------------
struct Hdf5RecordSink::Impl {
  File file;
  bool header_written = false;

  // ---- control events (/events, append-only) ----
  struct ControlRow {
    std::uint8_t kind;  // RecordingControlKind
    const char* recording_session_id;
    std::uint64_t host_time_ns;
    const char* request_id;
    std::uint64_t discarded_task_events;
    std::uint64_t discarded_reference_frames;
    std::uint8_t discarded_counts_complete;
  };
  hsize_t control_rows = 0;  // running length of the extensible /events dataset

  // ---- transitions (/transitions, whole-collection) ----
  struct TransitionRow {
    std::uint32_t protocol_version;
    const char* definition_id;
    std::uint64_t definition_revision;
    const char* definition_hash;
    const char* app_session_id;
    std::uint64_t run_sequence;
    std::uint64_t event_sequence;
    std::uint64_t transition_sequence;
    std::uint8_t event_kind;  // TaskEventKind
    std::uint32_t transition_id;
    std::uint32_t previous_state_id;
    std::uint32_t current_state_id;
    const char* trigger_kind;
    const char* trigger_source;
    const char* request_id;
    std::uint64_t proposal_receipt_sequence;
    std::uint64_t proposal_receipt_time_ns;
    const char* effective_source_id;
    std::uint64_t effective_sequence_number;
    std::uint64_t effective_timestamp_ns;
    std::uint64_t host_receive_time_ns;
  };

  // ---- intervals (/intervals, whole-collection) ----
  struct IntervalRow {
    const char* app_session_id;
    std::uint64_t run_sequence;
    std::uint32_t state_id;
    const char* start_source_id;
    std::uint64_t start_sequence_number;
    std::uint64_t start_timestamp_ns;
    std::uint8_t has_end;  // 0 when the state is still live
    const char* end_source_id;
    std::uint64_t end_sequence_number;
    std::uint64_t end_timestamp_ns;
    std::uint8_t complete;
  };

  // ---- diagnostics (/diagnostics, whole-collection) ----
  struct DiagnosticRow {
    std::uint8_t kind;  // TimelineDiagnosticKind
    std::uint64_t observed;
    std::uint64_t expected;
    const char* detail;
  };

  // ---- clock epochs (/clock_epochs, whole-collection) ----
  // Faithful to ClockModel + its embedded ClockUncertaintyBudget.  The optional
  // valid_until_reference_time_ns is stored as a value plus a has_ flag so an
  // absent (still-open) bound is never confused with a real zero.
  struct ClockRow {
    std::uint64_t epoch_id;
    std::uint64_t model_id;
    std::uint64_t valid_from_source_tick;
    std::uint64_t valid_from_reference_time_ns;
    std::uint8_t has_valid_until;
    std::uint64_t valid_until_reference_time_ns;
    std::uint64_t last_update_reference_time_ns;
    std::uint64_t last_update_age_ns;
    double slope_ns_per_source_tick;
    double offset_ns_at_anchor;
    double drift_ppm;
    std::uint64_t sync_sample_count;
    std::uint64_t rtt_min_ns;
    std::uint64_t rtt_median_ns;
    std::uint64_t rtt_max_ns;
    double residual_rms_ns;
    double max_residual_ns;
    std::uint64_t unc_quantization_ns;
    std::uint64_t unc_timestamp_jitter_ns;
    std::uint64_t unc_sync_dispersion_ns;
    std::uint64_t unc_sync_asymmetry_ns;
    std::uint64_t unc_fit_residual_ns;
    std::uint64_t unc_drift_extrapolation_ns;
    std::uint64_t unc_batching_ambiguity_ns;
    std::uint64_t unc_rounding_ns;
    std::uint64_t unc_total_ns;
    std::uint8_t locked;
  };

  // Compound datatype builders.  Each returns a fresh type the caller owns; the
  // string members reference the process-wide vlen string type, copied in so the
  // returned compound type is self-contained.
  static DataType make_control_type();
  static DataType make_transition_type();
  static DataType make_interval_type();
  static DataType make_diagnostic_type();
  static DataType make_clock_type();

  // Append rows to an extensible 1-D dataset, creating it (chunked, unlimited)
  // on first use.  Used for /events.
  bool append_rows(const char* name, hid_t type, const void* rows,
                   hsize_t count, hsize_t& running_length);

  // Replace a whole-collection dataset with `rows`.  Deletes any prior dataset
  // so each flush re-emits the authoritative collection; a zero-row collection
  // is written as an empty dataset so its absence-of-rows is explicit.
  bool replace_rows(const char* name, hid_t type, const void* rows,
                    hsize_t count, size_t row_size);

  bool flush_file() {
    return file && H5Fflush(file.get(), H5F_SCOPE_GLOBAL) >= 0;
  }
};

// ---- compound datatype construction ---------------------------------------

DataType Hdf5RecordSink::Impl::make_control_type() {
  DataType str = make_vlen_string_type();
  if (!str) return DataType();
  DataType t(H5Tcreate(H5T_COMPOUND, sizeof(ControlRow)));
  if (!t) return DataType();
  hid_t h = t.get();
  bool ok = true;
  ok &= insert_member(h, "kind", HOFFSET(ControlRow, kind), H5T_STD_U8LE);
  ok &= insert_member(h, "recording_session_id",
                      HOFFSET(ControlRow, recording_session_id), str.get());
  ok &= insert_member(h, "host_time_ns", HOFFSET(ControlRow, host_time_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "request_id", HOFFSET(ControlRow, request_id),
                      str.get());
  ok &= insert_member(h, "discarded_task_events",
                      HOFFSET(ControlRow, discarded_task_events), H5T_STD_U64LE);
  ok &= insert_member(h, "discarded_reference_frames",
                      HOFFSET(ControlRow, discarded_reference_frames),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "discarded_counts_complete",
                      HOFFSET(ControlRow, discarded_counts_complete),
                      H5T_STD_U8LE);
  return ok ? std::move(t) : DataType();
}

DataType Hdf5RecordSink::Impl::make_transition_type() {
  DataType str = make_vlen_string_type();
  if (!str) return DataType();
  DataType t(H5Tcreate(H5T_COMPOUND, sizeof(TransitionRow)));
  if (!t) return DataType();
  hid_t h = t.get();
  bool ok = true;
  ok &= insert_member(h, "protocol_version",
                      HOFFSET(TransitionRow, protocol_version), H5T_STD_U32LE);
  ok &= insert_member(h, "definition_id",
                      HOFFSET(TransitionRow, definition_id), str.get());
  ok &= insert_member(h, "definition_revision",
                      HOFFSET(TransitionRow, definition_revision),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "definition_hash",
                      HOFFSET(TransitionRow, definition_hash), str.get());
  ok &= insert_member(h, "app_session_id",
                      HOFFSET(TransitionRow, app_session_id), str.get());
  ok &= insert_member(h, "run_sequence", HOFFSET(TransitionRow, run_sequence),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "event_sequence",
                      HOFFSET(TransitionRow, event_sequence), H5T_STD_U64LE);
  ok &= insert_member(h, "transition_sequence",
                      HOFFSET(TransitionRow, transition_sequence),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "event_kind", HOFFSET(TransitionRow, event_kind),
                      H5T_STD_U8LE);
  ok &= insert_member(h, "transition_id",
                      HOFFSET(TransitionRow, transition_id), H5T_STD_U32LE);
  ok &= insert_member(h, "previous_state_id",
                      HOFFSET(TransitionRow, previous_state_id), H5T_STD_U32LE);
  ok &= insert_member(h, "current_state_id",
                      HOFFSET(TransitionRow, current_state_id), H5T_STD_U32LE);
  ok &= insert_member(h, "trigger_kind", HOFFSET(TransitionRow, trigger_kind),
                      str.get());
  ok &= insert_member(h, "trigger_source",
                      HOFFSET(TransitionRow, trigger_source), str.get());
  ok &= insert_member(h, "request_id", HOFFSET(TransitionRow, request_id),
                      str.get());
  ok &= insert_member(h, "proposal_receipt_sequence",
                      HOFFSET(TransitionRow, proposal_receipt_sequence),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "proposal_receipt_time_ns",
                      HOFFSET(TransitionRow, proposal_receipt_time_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "effective_source_id",
                      HOFFSET(TransitionRow, effective_source_id), str.get());
  ok &= insert_member(h, "effective_sequence_number",
                      HOFFSET(TransitionRow, effective_sequence_number),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "effective_timestamp_ns",
                      HOFFSET(TransitionRow, effective_timestamp_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "host_receive_time_ns",
                      HOFFSET(TransitionRow, host_receive_time_ns),
                      H5T_STD_U64LE);
  return ok ? std::move(t) : DataType();
}

DataType Hdf5RecordSink::Impl::make_interval_type() {
  DataType str = make_vlen_string_type();
  if (!str) return DataType();
  DataType t(H5Tcreate(H5T_COMPOUND, sizeof(IntervalRow)));
  if (!t) return DataType();
  hid_t h = t.get();
  bool ok = true;
  ok &= insert_member(h, "app_session_id",
                      HOFFSET(IntervalRow, app_session_id), str.get());
  ok &= insert_member(h, "run_sequence", HOFFSET(IntervalRow, run_sequence),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "state_id", HOFFSET(IntervalRow, state_id),
                      H5T_STD_U32LE);
  ok &= insert_member(h, "start_source_id",
                      HOFFSET(IntervalRow, start_source_id), str.get());
  ok &= insert_member(h, "start_sequence_number",
                      HOFFSET(IntervalRow, start_sequence_number),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "start_timestamp_ns",
                      HOFFSET(IntervalRow, start_timestamp_ns), H5T_STD_U64LE);
  ok &= insert_member(h, "has_end", HOFFSET(IntervalRow, has_end),
                      H5T_STD_U8LE);
  ok &= insert_member(h, "end_source_id",
                      HOFFSET(IntervalRow, end_source_id), str.get());
  ok &= insert_member(h, "end_sequence_number",
                      HOFFSET(IntervalRow, end_sequence_number), H5T_STD_U64LE);
  ok &= insert_member(h, "end_timestamp_ns",
                      HOFFSET(IntervalRow, end_timestamp_ns), H5T_STD_U64LE);
  ok &= insert_member(h, "complete", HOFFSET(IntervalRow, complete),
                      H5T_STD_U8LE);
  return ok ? std::move(t) : DataType();
}

DataType Hdf5RecordSink::Impl::make_diagnostic_type() {
  DataType str = make_vlen_string_type();
  if (!str) return DataType();
  DataType t(H5Tcreate(H5T_COMPOUND, sizeof(DiagnosticRow)));
  if (!t) return DataType();
  hid_t h = t.get();
  bool ok = true;
  ok &= insert_member(h, "kind", HOFFSET(DiagnosticRow, kind), H5T_STD_U8LE);
  ok &= insert_member(h, "observed", HOFFSET(DiagnosticRow, observed),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "expected", HOFFSET(DiagnosticRow, expected),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "detail", HOFFSET(DiagnosticRow, detail), str.get());
  return ok ? std::move(t) : DataType();
}

DataType Hdf5RecordSink::Impl::make_clock_type() {
  DataType t(H5Tcreate(H5T_COMPOUND, sizeof(ClockRow)));
  if (!t) return DataType();
  hid_t h = t.get();
  bool ok = true;
  ok &= insert_member(h, "epoch_id", HOFFSET(ClockRow, epoch_id),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "model_id", HOFFSET(ClockRow, model_id),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "valid_from_source_tick",
                      HOFFSET(ClockRow, valid_from_source_tick), H5T_STD_U64LE);
  ok &= insert_member(h, "valid_from_reference_time_ns",
                      HOFFSET(ClockRow, valid_from_reference_time_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "has_valid_until",
                      HOFFSET(ClockRow, has_valid_until), H5T_STD_U8LE);
  ok &= insert_member(h, "valid_until_reference_time_ns",
                      HOFFSET(ClockRow, valid_until_reference_time_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "last_update_reference_time_ns",
                      HOFFSET(ClockRow, last_update_reference_time_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "last_update_age_ns",
                      HOFFSET(ClockRow, last_update_age_ns), H5T_STD_U64LE);
  ok &= insert_member(h, "slope_ns_per_source_tick",
                      HOFFSET(ClockRow, slope_ns_per_source_tick),
                      H5T_IEEE_F64LE);
  ok &= insert_member(h, "offset_ns_at_anchor",
                      HOFFSET(ClockRow, offset_ns_at_anchor), H5T_IEEE_F64LE);
  ok &= insert_member(h, "drift_ppm", HOFFSET(ClockRow, drift_ppm),
                      H5T_IEEE_F64LE);
  ok &= insert_member(h, "sync_sample_count",
                      HOFFSET(ClockRow, sync_sample_count), H5T_STD_U64LE);
  ok &= insert_member(h, "rtt_min_ns", HOFFSET(ClockRow, rtt_min_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "rtt_median_ns", HOFFSET(ClockRow, rtt_median_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "rtt_max_ns", HOFFSET(ClockRow, rtt_max_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "residual_rms_ns",
                      HOFFSET(ClockRow, residual_rms_ns), H5T_IEEE_F64LE);
  ok &= insert_member(h, "max_residual_ns",
                      HOFFSET(ClockRow, max_residual_ns), H5T_IEEE_F64LE);
  ok &= insert_member(h, "unc_quantization_ns",
                      HOFFSET(ClockRow, unc_quantization_ns), H5T_STD_U64LE);
  ok &= insert_member(h, "unc_timestamp_jitter_ns",
                      HOFFSET(ClockRow, unc_timestamp_jitter_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "unc_sync_dispersion_ns",
                      HOFFSET(ClockRow, unc_sync_dispersion_ns), H5T_STD_U64LE);
  ok &= insert_member(h, "unc_sync_asymmetry_ns",
                      HOFFSET(ClockRow, unc_sync_asymmetry_ns), H5T_STD_U64LE);
  ok &= insert_member(h, "unc_fit_residual_ns",
                      HOFFSET(ClockRow, unc_fit_residual_ns), H5T_STD_U64LE);
  ok &= insert_member(h, "unc_drift_extrapolation_ns",
                      HOFFSET(ClockRow, unc_drift_extrapolation_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "unc_batching_ambiguity_ns",
                      HOFFSET(ClockRow, unc_batching_ambiguity_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "unc_rounding_ns",
                      HOFFSET(ClockRow, unc_rounding_ns), H5T_STD_U64LE);
  ok &= insert_member(h, "unc_total_ns", HOFFSET(ClockRow, unc_total_ns),
                      H5T_STD_U64LE);
  ok &= insert_member(h, "locked", HOFFSET(ClockRow, locked), H5T_STD_U8LE);
  return ok ? std::move(t) : DataType();
}

// ---- dataset helpers -------------------------------------------------------

bool Hdf5RecordSink::Impl::append_rows(const char* name, hid_t type,
                                       const void* rows, hsize_t count,
                                       hsize_t& running_length) {
  if (running_length == 0) {
    // Create the dataset unlimited-along-dim-0 and chunked so it can grow one
    // control event at a time.
    const hsize_t initial[1] = {count};
    const hsize_t maxdims[1] = {H5S_UNLIMITED};
    DataSpace space(H5Screate_simple(1, initial, maxdims));
    if (!space) return false;
    PropList create(H5Pcreate(H5P_DATASET_CREATE));
    if (!create) return false;
    const hsize_t chunk[1] = {8};  // control events are few; a small chunk fits
    if (H5Pset_chunk(create.get(), 1, chunk) < 0) return false;
    DataSet dset(H5Dcreate2(file.get(), name, type, space.get(), H5P_DEFAULT,
                            create.get(), H5P_DEFAULT));
    if (!dset) return false;
    if (count > 0 &&
        H5Dwrite(dset.get(), type, H5S_ALL, H5S_ALL, H5P_DEFAULT, rows) < 0) {
      return false;
    }
    running_length = count;
    return true;
  }

  // Extend the existing dataset and write into the new trailing slab.
  DataSet dset(H5Dopen2(file.get(), name, H5P_DEFAULT));
  if (!dset) return false;
  const hsize_t new_length = running_length + count;
  const hsize_t new_dims[1] = {new_length};
  if (H5Dset_extent(dset.get(), new_dims) < 0) return false;
  DataSpace file_space(H5Dget_space(dset.get()));
  if (!file_space) return false;
  const hsize_t start[1] = {running_length};
  const hsize_t block[1] = {count};
  if (H5Sselect_hyperslab(file_space.get(), H5S_SELECT_SET, start, nullptr,
                          block, nullptr) < 0) {
    return false;
  }
  DataSpace mem_space(H5Screate_simple(1, block, nullptr));
  if (!mem_space) return false;
  if (H5Dwrite(dset.get(), type, mem_space.get(), file_space.get(),
               H5P_DEFAULT, rows) < 0) {
    return false;
  }
  running_length = new_length;
  return true;
}

bool Hdf5RecordSink::Impl::replace_rows(const char* name, hid_t type,
                                        const void* rows, hsize_t count,
                                        size_t /*row_size*/) {
  // Each flush re-emits the whole collection, so remove any prior dataset and
  // recreate it at the current row count.  (HDF5 does not reclaim the freed
  // space in-file, but recordings are bounded and a self-consistent, readable
  // snapshot after every flush is the property that matters here.)
  if (H5Lexists(file.get(), name, H5P_DEFAULT) > 0) {
    if (H5Ldelete(file.get(), name, H5P_DEFAULT) < 0) return false;
  }
  const hsize_t dims[1] = {count};
  DataSpace space(H5Screate_simple(1, dims, nullptr));
  if (!space) return false;
  DataSet dset(H5Dcreate2(file.get(), name, type, space.get(), H5P_DEFAULT,
                          H5P_DEFAULT, H5P_DEFAULT));
  if (!dset) return false;
  if (count > 0 &&
      H5Dwrite(dset.get(), type, H5S_ALL, H5S_ALL, H5P_DEFAULT, rows) < 0) {
    return false;
  }
  return true;
}

// ---- Hdf5RecordSink --------------------------------------------------------

Hdf5RecordSink::Hdf5RecordSink(std::string path)
    : path_(std::move(path)), impl_(std::make_unique<Impl>()) {}

Hdf5RecordSink::~Hdf5RecordSink() = default;

bool Hdf5RecordSink::open(const std::string& schema_version,
                          std::uint64_t created_host_time_ns,
                          const std::string& metadata_json) {
  if (impl_->header_written || impl_->file) {
    return false;  // open is a once-only header write
  }
  impl_->file = File(H5Fcreate(path_.c_str(), H5F_ACC_TRUNC, H5P_DEFAULT,
                               H5P_DEFAULT));
  if (!impl_->file) return false;
  const hid_t root = impl_->file.get();
  if (!write_string_attribute(root, "schema_version", schema_version)) {
    return false;
  }
  if (!write_string_attribute(root, "format", "sciencexyz.task_timeline.hdf5")) {
    return false;
  }
  if (!write_u64_attribute(root, "created_host_time_ns", created_host_time_ns)) {
    return false;
  }
  if (!write_string_attribute(root, "metadata_json", metadata_json)) {
    return false;
  }
  // A recording is incomplete until a flush proves otherwise; write the flag now
  // so a file that never flushes is not mistaken for a complete timeline.
  if (!write_u8_attribute(root, "timeline_complete", 0)) {
    return false;
  }
  impl_->header_written = true;
  return impl_->flush_file();
}

bool Hdf5RecordSink::write_control_event(const RecordingControlEvent& event) {
  if (!impl_->file) return false;
  Impl::ControlRow row;
  row.kind = static_cast<std::uint8_t>(event.kind);
  row.recording_session_id = event.recording_session_id.c_str();
  row.host_time_ns = event.host_time_ns;
  row.request_id = event.request_id.c_str();
  row.discarded_task_events = event.discarded_task_events;
  row.discarded_reference_frames = event.discarded_reference_frames;
  row.discarded_counts_complete = event.discarded_counts_complete ? 1 : 0;

  DataType type = Impl::make_control_type();
  if (!type) return false;
  if (!impl_->append_rows("/events", type.get(), &row, 1,
                          impl_->control_rows)) {
    return false;
  }
  return impl_->flush_file();
}

bool Hdf5RecordSink::write_transition_records(
    const std::vector<TaskTransitionRecord>& records) {
  if (!impl_->file) return false;
  std::vector<Impl::TransitionRow> rows(records.size());
  for (std::size_t i = 0; i < records.size(); ++i) {
    const TaskTransitionRecord& r = records[i];
    Impl::TransitionRow& out = rows[i];
    out.protocol_version = r.protocol_version;
    out.definition_id = r.definition_id.c_str();
    out.definition_revision = r.definition_revision;
    out.definition_hash = r.definition_hash.c_str();
    out.app_session_id = r.app_session_id.c_str();
    out.run_sequence = r.run_sequence;
    out.event_sequence = r.event_sequence;
    out.transition_sequence = r.transition_sequence;
    out.event_kind = static_cast<std::uint8_t>(r.event_kind);
    out.transition_id = r.transition_id;
    out.previous_state_id = r.previous_state_id;
    out.current_state_id = r.current_state_id;
    out.trigger_kind = r.trigger_kind.c_str();
    out.trigger_source = r.trigger_source.c_str();
    out.request_id = r.request_id.c_str();
    out.proposal_receipt_sequence = r.proposal_receipt_sequence;
    out.proposal_receipt_time_ns = r.proposal_receipt_time_ns;
    out.effective_source_id = r.effective_frame.source_id.c_str();
    out.effective_sequence_number = r.effective_frame.sequence_number;
    out.effective_timestamp_ns = r.effective_frame.timestamp_ns;
    out.host_receive_time_ns = r.host_receive_time_ns;
  }
  DataType type = Impl::make_transition_type();
  if (!type) return false;
  if (!impl_->replace_rows("/transitions", type.get(), rows.data(),
                           rows.size(), sizeof(Impl::TransitionRow))) {
    return false;
  }
  return impl_->flush_file();
}

bool Hdf5RecordSink::write_intervals(
    const std::vector<TaskInterval>& intervals) {
  if (!impl_->file) return false;
  std::vector<Impl::IntervalRow> rows(intervals.size());
  for (std::size_t i = 0; i < intervals.size(); ++i) {
    const TaskInterval& iv = intervals[i];
    Impl::IntervalRow& out = rows[i];
    out.app_session_id = iv.app_session_id.c_str();
    out.run_sequence = iv.run_sequence;
    out.state_id = iv.state_id;
    out.start_source_id = iv.start.source_id.c_str();
    out.start_sequence_number = iv.start.sequence_number;
    out.start_timestamp_ns = iv.start.timestamp_ns;
    if (iv.end.has_value()) {
      out.has_end = 1;
      out.end_source_id = iv.end->source_id.c_str();
      out.end_sequence_number = iv.end->sequence_number;
      out.end_timestamp_ns = iv.end->timestamp_ns;
    } else {
      // A still-live state: zeroed bound plus has_end=0 so a reader never treats
      // the placeholder as a real end frame.
      out.has_end = 0;
      out.end_source_id = "";
      out.end_sequence_number = 0;
      out.end_timestamp_ns = 0;
    }
    out.complete = iv.complete ? 1 : 0;
  }
  DataType type = Impl::make_interval_type();
  if (!type) return false;
  if (!impl_->replace_rows("/intervals", type.get(), rows.data(), rows.size(),
                           sizeof(Impl::IntervalRow))) {
    return false;
  }
  return impl_->flush_file();
}

bool Hdf5RecordSink::write_diagnostics(
    const std::vector<TimelineDiagnostic>& diagnostics) {
  if (!impl_->file) return false;
  std::vector<Impl::DiagnosticRow> rows(diagnostics.size());
  for (std::size_t i = 0; i < diagnostics.size(); ++i) {
    const TimelineDiagnostic& d = diagnostics[i];
    Impl::DiagnosticRow& out = rows[i];
    out.kind = static_cast<std::uint8_t>(d.kind);
    out.observed = d.observed;
    out.expected = d.expected;
    out.detail = d.detail.c_str();
  }
  DataType type = Impl::make_diagnostic_type();
  if (!type) return false;
  if (!impl_->replace_rows("/diagnostics", type.get(), rows.data(),
                           rows.size(), sizeof(Impl::DiagnosticRow))) {
    return false;
  }
  return impl_->flush_file();
}

bool Hdf5RecordSink::write_clock_epochs(
    const std::vector<wireless::ClockModel>& epochs) {
  if (!impl_->file) return false;
  std::vector<Impl::ClockRow> rows(epochs.size());
  for (std::size_t i = 0; i < epochs.size(); ++i) {
    const wireless::ClockModel& m = epochs[i];
    Impl::ClockRow& out = rows[i];
    out.epoch_id = m.epoch_id;
    out.model_id = m.model_id;
    out.valid_from_source_tick = m.valid_from_source_tick;
    out.valid_from_reference_time_ns = m.valid_from_reference_time_ns;
    if (m.valid_until_reference_time_ns.has_value()) {
      out.has_valid_until = 1;
      out.valid_until_reference_time_ns = *m.valid_until_reference_time_ns;
    } else {
      out.has_valid_until = 0;
      out.valid_until_reference_time_ns = 0;
    }
    out.last_update_reference_time_ns = m.last_update_reference_time_ns;
    out.last_update_age_ns = m.last_update_age_ns;
    out.slope_ns_per_source_tick = m.slope_ns_per_source_tick;
    out.offset_ns_at_anchor = m.offset_ns_at_anchor;
    out.drift_ppm = m.drift_ppm;
    out.sync_sample_count = static_cast<std::uint64_t>(m.sync_sample_count);
    out.rtt_min_ns = m.rtt_min_ns;
    out.rtt_median_ns = m.rtt_median_ns;
    out.rtt_max_ns = m.rtt_max_ns;
    out.residual_rms_ns = m.residual_rms_ns;
    out.max_residual_ns = m.max_residual_ns;
    out.unc_quantization_ns = m.uncertainty.quantization_ns;
    out.unc_timestamp_jitter_ns = m.uncertainty.timestamp_jitter_ns;
    out.unc_sync_dispersion_ns = m.uncertainty.sync_dispersion_ns;
    out.unc_sync_asymmetry_ns = m.uncertainty.sync_asymmetry_ns;
    out.unc_fit_residual_ns = m.uncertainty.fit_residual_ns;
    out.unc_drift_extrapolation_ns = m.uncertainty.drift_extrapolation_ns;
    out.unc_batching_ambiguity_ns = m.uncertainty.batching_ambiguity_ns;
    out.unc_rounding_ns = m.uncertainty.rounding_ns;
    // Persist the estimator's own aggregate rather than recomputing it, so the
    // stored total matches what the running clock reported at record time.
    out.unc_total_ns = m.uncertainty.total_ns();
    out.locked = m.locked ? 1 : 0;
  }
  DataType type = Impl::make_clock_type();
  if (!type) return false;
  if (!impl_->replace_rows("/clock_epochs", type.get(), rows.data(),
                           rows.size(), sizeof(Impl::ClockRow))) {
    return false;
  }
  return impl_->flush_file();
}

bool Hdf5RecordSink::write_timeline_complete(bool complete) {
  if (!impl_->file) return false;
  if (!write_u8_attribute(impl_->file.get(), "timeline_complete",
                          complete ? 1 : 0)) {
    return false;
  }
  return impl_->flush_file();
}

bool Hdf5RecordSink::close() {
  if (!impl_->file) return false;
  const bool flushed = impl_->flush_file();
  impl_->file.reset();  // H5Fclose
  return flushed;
}

}  // namespace app::recording
