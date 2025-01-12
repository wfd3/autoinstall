#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
import tempfile
import shutil
import urllib.request
from pathlib import Path
import re
from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Optional

# ----------------------------------------------------------------------------
# Constants and Configuration
# ----------------------------------------------------------------------------

VERSION = "1.1"

# ANSI color codes for output
COLORS = {
    'INFO': '\033[94m',    # Blue
    'SUCCESS': '\033[92m', # Green
    'WARNING': '\033[93m', # Yellow
    'ERROR': '\033[91m',   # Red
    'END': '\033[0m'
}

# Package data structures
@dataclass
class PackageAttributes:
    name: str
    install_method: str = 'apt'  # 'apt' or 'deb'
    deb: Optional[str] = None    # for deb downloads
    repositories: Set[str] = field(default_factory=set)
    sources: List[tuple[str, str]] = field(default_factory=list)  # [(filename, content),...]
    pre_scripts: List[str] = field(default_factory=list)
    post_scripts: List[str] = field(default_factory=list)
    flags: Set[str] = field(default_factory=set)
    
@dataclass
class PackageList:
   packages: Dict[str, PackageAttributes]
   start_packages: Dict[str, PackageAttributes]
   end_packages: Dict[str, PackageAttributes]

   def is_empty(self) -> bool:
       return (not self.packages and 
               not self.start_packages and 
               not self.end_packages)

# Globals
temp_dir = None

# ----------------------------------------------------------------------------
# Output Functions
# ----------------------------------------------------------------------------

def colored_output(msg: str, color: str, prefix: str = "==>", is_error: bool = False):
    """Print colored output with prefix."""
    stream = sys.stderr if is_error else sys.stdout
    print(f"{COLORS[color]}{prefix} {msg}{COLORS['END']}", file=stream, flush=True)

def info(msg: str):
    colored_output(msg, 'INFO')

def success(msg: str):
    colored_output(msg, 'SUCCESS')

def warning(msg: str):
    colored_output(msg, 'WARNING', "==> WARNING:")

def error(msg: str):
    colored_output(msg, 'ERROR', "==> ERROR:", True)

# ----------------------------------------------------------------------------
# Environment Management
# ----------------------------------------------------------------------------

def make_working_directory():
    """Create and manage a temporary working directory."""

    global temp_dir 
    
    temp_dir = tempfile.mkdtemp(suffix='_pkg_install')
    try:
        os.chdir(temp_dir)
    except OSError as e:
        bail(f"Failed to change to temp directory: {e}")

def bail(message: str = None, exit_code: int = 1):
    """Exit the program with optional error message."""
    if message:
        error(message)

    if temp_dir:
        if not args.preserve:
            shutil.rmtree(temp_dir, ignore_errors=True)
        elif message:
            info(f"Preserving working directory: {temp_dir}")

    sys.exit(exit_code)

def root_check():
    """Verify root privileges when not in dry-run mode."""
    if os.geteuid() != 0 and not args.dryrun:
        bail("This script must be run as root")

# ----------------------------------------------------------------------------
# Install package database
# ----------------------------------------------------------------------------
def load_installed_packages() -> Set[str]:
    """Load the list of installed packages."""
    try:
        result = subprocess.run(['apt', 'list', '--installed'],
                             capture_output=True, text=True, check=True)
        return {p.split('/')[0] for p in result.stdout.splitlines()[1:] if '/' in p}
    except subprocess.CalledProcessError:
        bail("Error getting list of installed packages")

def is_package_installed(package: str) -> bool:
    """Check if a package is already installed."""
    return package in installed_packages

# ----------------------------------------------------------------------------
# Package Processing Functions
# ----------------------------------------------------------------------------

def parse_package_entry(name: str, lines: List[str]) -> PackageAttributes:
    """Parse a single package entry and return its attributes and type."""
    pkg = PackageAttributes(name=name)
    
    for line in lines:
        directive_type, content = line.split(':', 1)
        directive_type = directive_type.strip()
        content = content.strip()
        
        match directive_type:
            case 'flags':
                flgs = {f.strip().lower() for f in content.split(',')}
                pkg.flags.update(flgs)
            case 'deb':
                pkg.install_method = 'deb'
                pkg.url = content
            case 'repo':
                pkg.repositories.add(content)
            case 'source':
                filename, source_content = content.strip().split(None, 1)
                if not filename.startswith('/etc/apt/sources.list.d/'):
                    filename = f"/etc/apt/sources.list.d/{filename}"
                pkg.sources.append((filename, source_content))
            case 'script':
                pkg.pre_scripts.append(content)
            case _ if directive_type.startswith('post_'):
                pkg.post_scripts.append(content)
            case _:
                bail(f"Unknown directive type: {directive_type}")
                
    return pkg

def parse_package_list(filename: Path) -> Dict[str, PackageAttributes]:
    """Parse package list file into PackageList structure."""
    packages = {}
        
    with open(filename) as f:
        current_pkg = None
        current_directives = []
        continued_line = ''
        
        for line in f:
            line = line.rstrip()
            
            if not line or line.startswith('#'):
                continue

            # Handle line continuation
            if line.endswith('\\'):
                continued_line += line[:-1]
                continue
                
            if continued_line:
                line = continued_line + line
                continued_line = ''

            # Package name starts at beginning of line
            if not line.startswith(' '):
                # Process previous package if any
                if current_pkg:
                    pkg = parse_package_entry(current_pkg, current_directives)
                    packages[current_pkg] = pkg

                # Start new package
                current_pkg = line.strip().rstrip(':')
                current_directives = []
                continue

            # Collect directive
            if not current_pkg:
                bail("Found directive without a package name")
                
            line = line.strip()
            if line:  # Only add non-empty directives
                current_directives.append(line)
    
    # Process last package if any
    if current_pkg:
        pkg = parse_package_entry(current_pkg, current_directives)
        packages[current_pkg] = pkg

    return packages

def filter_packages(pkg_list: Dict[str, PackageAttributes]) -> PackageList:
    """Filter packages into start, end and regular packages.  Apply flags as needed
    """
    to_install = {}
    start_packages = {}
    end_packages = {}
   
    for name, pkg in pkg_list.items():
        
        if args.start_only and 'start' not in pkg.flags:
            info(f"Skipping non-start package: {name}")
            continue
        if args.end_only and 'end' not in pkg.flags:   
            info(f"Skipping non-end package: {name}")
            continue

        if args.skip_start and 'start' in pkg.flags:
            info(f"Skipping start package: {name}")
            continue
        if args.skip_end and 'end' in pkg.flags:
            info(f"Skipping end package: {name}")
            continue
        
        if is_package_installed(name) and 'force' not in pkg.flags:
            info(f"Skipping already installed package: {name}")
            continue

        if 'skip' in pkg.flags:
            info(f"Skipping package: {name}")
            continue

        if 'start' in pkg.flags and not args.skip_start:
            info(f"{'Force installing' if 'force' in pkg.flags else 'Adding'} to start: {name}")
            start_packages[name] = pkg
        elif 'end' in pkg.flags and not args.skip_end:
            info(f"{'Force installing' if 'force' in pkg.flags else 'Adding'} to end: {name}")
            end_packages[name] = pkg
        else:
            to_install[name] = pkg
            info(f"{'Force installing' if 'force' in pkg.flags else 'Adding'} to regular: {name}")
                    
    return PackageList(
       packages=to_install,
       start_packages=start_packages,
       end_packages=end_packages
   )

# ----------------------------------------------------------------------------
# Pretty printers
# ----------------------------------------------------------------------------

def format_package_list(packages: Set[str], min_spacing: int = 2) -> str:
    """Format package list into columns for display."""
    if not packages:
        return ""

    try:
        terminal_width = os.get_terminal_size().columns
    except (AttributeError, OSError):
        terminal_width = 80

    pkg_list = sorted(packages)
    max_length = max(len(pkg) for pkg in pkg_list)
    column_width = max_length + min_spacing
    num_columns = max(1, terminal_width // column_width)

    num_rows = (len(pkg_list) + num_columns - 1) // num_columns

    output = []
    for row in range(num_rows):
        line = []
        for col in range(num_columns):
            idx = col * num_rows + row
            if idx < len(pkg_list):
                line.append(pkg_list[idx].ljust(column_width))
        output.append("".join(line).rstrip())

    return "\n".join(output)

def pretty_print_scripts(script: str):
    """Format script commands for display."""
    for line in re.split(r'(?<=;)', script):
        print(f"  {line.lstrip()}")

# ----------------------------------------------------------------------------
# Package Installation Functions
# ----------------------------------------------------------------------------

def run_scripts(scripts: List[Tuple[str, str]], phase: str = ""):
    """Run a list of (script, package) pairs."""
    for script, package in scripts:
        if args.dryrun:
            info(f"Would run {phase} script for {package}:")
            pretty_print_scripts(script)
            continue
            
        info(f"Running {phase} script for {package}:")
        pretty_print_scripts(script)
        try:
            subprocess.run(script, **run_opts, shell=True, check=True)
            success(f"Completed {phase} script for {package}")
        except subprocess.CalledProcessError:
            bail(f"Error running {phase} script: {script}")    

def download_packages(to_download: Dict[str, PackageAttributes]) -> Dict[str, str]:
    """Download packages from URLs.

    Returns dict mapping package names to downloaded filenames.
    """
    downloaded_files = {}

    for name, package in to_download.items():
        url = package.url

        if args.dryrun:
            info(f"Would download: {url} for package {name}")
            continue

        try:
            filename = url.split('/')[-1]
            info(f"Downloading {url}")
            info(f"    to file {filename}")
            opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
            urllib.request.install_opener(opener)           
            urllib.request.urlretrieve(url, filename)
        
            if not os.path.isfile(filename):
                bail(f"Downloaded file {filename} not found")

            downloaded_files[name] = filename
            success(f"Downloaded {filename} successfully")

        except Exception as e:
            bail(f"Error downloading from URL {url}: {e}")

    return downloaded_files

def add_repositories(repos: Set[str]) -> bool:
    """Add repositories. Returns True if updates needed."""
    if not repos:
        return False

    update_needed = False

    for repo in repos:
        if args.dryrun:
            info(f"Would add repository: {repo}")
            continue

        try:
            info(f"Adding repository: {repo}")
            subprocess.run(['add-apt-repository', '-y', repo], **run_opts, check=True)
            success(f"Added repository: {repo}")
            update_needed = True
        except subprocess.CalledProcessError:
            bail(f"Error adding repository: {repo}")

    return update_needed

def add_sources(sources: List[Tuple[str, str]]) -> bool:
    """Add sources to /apt/sources.list.d/... Returns True if update needed."""

    if not sources:
        return False
    
    update_needed = False

    for filename, content in sources:
        if args.dryrun:
            info(f"Would add '{content}' to {filename}")
            continue

        try:
            if not os.path.exists(filename):
                info(f"Adding source file: {filename}")
                with open(filename, 'w') as f:
                    f.write(f"{content}\n")
                success(f"Added package source in {filename}")
                update_needed = True
            else:
                warning(f"'{filename}' exists, skipping")
        except Exception as e:
            bail(f"Error adding source to {filename}: {e}")

    return update_needed

def install_apt_packages(packages: Set[str]):
    """Install packages via apt."""
    if not packages:
        return 
    
    for package in packages:
        if args.dryrun:
            info(f"Would install apt package: {package}")
            continue 

        try:
            subprocess.run(['apt', '-y', 'install'] + list(packages), **run_opts, check=True)
            success("Package installation completed successfully")
        except subprocess.CalledProcessError:
            bail("Error installing packages")

def install_deb_packages(downloaded_files: Dict[str, str]):
    """Install downloaded .deb packages."""
    for package, filename in downloaded_files.items():
        if args.dryrun:
            info(f"Would install .deb file {filename} for package {package}")
            continue

        info(f"Installing .deb file {filename} for package {package}")
        try:
            subprocess.run(['dpkg', '-i', filename], **run_opts, check=True)
            success(f"Installed {filename} successfully")
        except subprocess.CalledProcessError:
            bail(f"Error installing {filename}")

def update_package_lists():
    """Update apt package lists."""
    if args.dryrun:
        info("Would update package lists")
        return

    info("Updating package lists")
    try:
        subprocess.run(['apt', 'update'], **run_opts, check=True)
        success("Package lists updated")
    except subprocess.CalledProcessError:
        bail("Error updating package lists")

# ----------------------------------------------------------------------------
# Instal a PackageList 
# ----------------------------------------------------------------------------

def run_installation(pkg_list: PackageList):
    """Process all installation steps for the given packages."""
   
    apt_update_needed = False

    # Run start scripts
    for name, pkg in pkg_list.start_packages.items():
        if pkg.pre_scripts:
           success("Running start scripts")
           run_scripts([(s, pkg.name) for s in pkg.pre_scripts], "start")

    # Collect all repositories and sources
    all_repos = set()
    all_sources = []
    for pkg in pkg_list.packages.values():
       all_repos.update(pkg.repositories)
       all_sources.extend(pkg.sources)

    # Add sources if needed
    if all_sources:
        success("Adding package sources")
        apt_update_needed |= add_sources(all_sources)

    # Add repositories if needed
    if all_repos or all_sources:
       success("Setting up package sources")
       apt_update_needed |= add_repositories(all_repos)
    
    # Collect and run pre-install scripts
    pre_scripts = []
    for pkg in pkg_list.packages.values():
       pre_scripts.extend((script, pkg.name) for script in pkg.pre_scripts)
    if pre_scripts:
       success("Running pre-install scripts")
       run_scripts(pre_scripts, "pre-install")

    if apt_update_needed:
           info(f"Repositories or Sources updated; running 'apt update'")
           update_package_lists()


    # Download and install .deb packages
    urls = {name: pkg for name, pkg in pkg_list.packages.items() 
            if pkg.url and pkg.install_method == "deb"}
    downloads = download_packages(urls)

    # Install the .debs just downloaded
    install_deb_packages(downloads)

    # Install apt packages
    apt_packages = {name for name, pkg in pkg_list.packages.items() 
                  if pkg.install_method == 'apt'}
    if apt_packages:
       success("Installing system packages")
       install_apt_packages(apt_packages)

    # Collect and run post-install scripts
    post_scripts = []
    for pkg in pkg_list.packages.values():
       post_scripts.extend((script, pkg.name) for script in pkg.post_scripts)
    if post_scripts:
       success("Running post-install scripts")
       run_scripts(post_scripts, "post-install")

    # Run end scripts
    for name, pkg in pkg_list.end_packages.items():
       if pkg.pre_scripts:
           success("Running end scripts")
           run_scripts([(s, pkg.name) for s in pkg.pre_scripts], "end")

# ----------------------------------------------------------------------------
# Main Program
# ----------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    global args

    parser = argparse.ArgumentParser(
        description='Install packages and manage package repository dependencies',
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('-n', '--dryrun', action='store_true',
                      help='Dry run (do not actually install packages)')
    parser.add_argument('-p', '--preserve', action='store_true',
                      help='Preserve the temporary working directory')
    parser.add_argument('-v', '--version', action='version',
                      version=f'%(prog)s version {VERSION}')
    parser.add_argument('filename', help='Package list file')
    parser.add_argument('--start-only', action='store_true',
                      help=f'Only run the START directives and exit')
    parser.add_argument('--end-only', action='store_true',
                      help=f'Only run the END directives and exit')
    
    parser.add_argument('--skip-start', action='store_true',
                      help=f'Do not run package with the "start" flag')
    parser.add_argument('--skip-end', action='store_true',
                      help=f'Do not run packages with the "end" flag')

    parser.add_argument('-q', '--quiet', action='store_true',
                        help='Squelch subprocess output')

    args = parser.parse_args()

    # Set up subprocess runargs
    global run_opts
    run_opts = {}
    if args.quiet:
        run_opts = {
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL
        }

    # Validate input file
    args.filename = Path(args.filename).resolve()
    if not args.filename.is_file():
        bail(f"Package file '{args.filename}' does not exist or is not readable.")

    return args


def main():
    global installed_packages, temp_dir, preserve_working_dir

    args = parse_arguments()

    installed_packages = load_installed_packages()
    package_entries = parse_package_list(args.filename)
    filtered_packages = filter_packages(package_entries)
    if filtered_packages.is_empty():
        success(f"No packages to install")
        return
  
    # Set up environment
    make_working_directory()
    if not args.dryrun:
        root_check()

    # Start installation process
    success("Starting package installation")
    run_installation(filtered_packages)
    success("Installation complete")

if __name__ == '__main__':
    #try:
    #    main()
    #except KeyboardInterrupt:
    #    bail("\nInstallation interrupted by user")
    #except Exception as e:
    #    if '--debug' in sys.argv:
    #        raise
    #    bail(f"Unexpected error: {e}")

    main()
    bail(exit_code=0)