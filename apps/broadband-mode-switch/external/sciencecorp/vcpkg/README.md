# vcpkg

A vcpkg port registry for Science.

## How to use

The ports in this registry are consumed as overlay ports. To use them...

Anywhere on your machine:

```bash
# clone this repo in your favorite location
# e.g. `git clone git@github.com:sciencecorp/vcpkg.git`

# cd into the repo
cd vcpkg

# add repo's `ports/` to path in current session (will not persist)
export PATH=$PATH:$(pwd)/ports

# or, persist (bash)
echo "export PATH=\$PATH:$(pwd)/ports" >> ~/.bashrc
source ~/.bashrc

# or, persist (zsh)
echo "export PATH=\$PATH:$(pwd)/ports" >> ~/.zshenv
source ~/.zshenv
```

Or, within a specific repo:

```bash
# Add this repo as a submodule
git submodule add git@github.com:sciencecorp/vcpkg.git

# Add this to your vcpkg.json
{
  ...,
  "vcpkg-configuration": {
    "overlay-ports": ["<path to>/vcpkg/ports"],
  }
}

# Or in a makefile, or before you run CMake, set
VCPKG_OVERLAY_PORTS=<path to your vcpkg submodule>/ports

# Then you'll be able to run
vcpkg install # as usual
```
