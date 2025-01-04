# Autoinstall 

Install apt packages from a list of packages.  Useful for bootstrapping a new machine.

Designed to have no dependencies beyond a basic Linux distro install. 

Basic usage:
```shell
  autoinstall package_list.cfg
```


### Package List File Format

1. **Basic Package Installation**:
   - Each line can contain the name of a package to be installed using `apt`.
   - Example:
     ```plaintext
     package_name
     ```

2. **Directives**:
   - Lines can contain directives to perform additional actions before installing a package.
   - The format for directives is `package_name:directive1:content1;directive2:content2;...`.
   - Supported directives:
     -- `repo`: Add a repository using `add-apt-repository` and then install `package_name`.
       - Example:
         ```plaintext
         package_name: repo: ppa:example/ppa
         ```
     - `url`: Download and install a package from a URL.
       - Example:
         ```plaintext
         package_name: url: http://example.com/package.deb
         ```
     - `script`: Run a custom script and then install `package_name`.
       - Example:
         ```plaintext
         package_name: script: echo "Custom script"
         ```
     - `source`: Add a Debian source to /etc/apt/sources.list.d/.
       - Example:
         ```plaintext
         package_name: source: custom.list deb http://example.com/repo stable main
         ```

3. **Comments and Empty Lines**:
   - Lines starting with `#` are treated as comments and ignored.
   - Empty lines are also ignored.

### Example Package List File

```plaintext
# This is a comment
#

# Install the curl package via `apt install`
curl

# Add a repository and install a my_package from it
my_package: repo: ppa:example/ppa

# Download downloaded_package.deb from a URL and install it.  
downloaded_package: url: http://example.com/downloaded_package.deb

# Add a Debian source in /etc/apt/sources.list.d and install custom_package from it.
custom_package: source: custom.list deb http://example.com/repo stable main

# Run a custom script before installing a `scripted_package`
scripted_package: script: echo "Running custom script"
```

More complete examples can be found in [packages.cfg](./packages.cfg)
