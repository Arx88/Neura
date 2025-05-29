#!/usr/bin/env python3
import os
import sys
import time
import platform
import subprocess
from getpass import getpass
import re
import json
import shutil

IS_WINDOWS = platform.system() == 'Windows'
if IS_WINDOWS:
    import winreg

# State persistence file
STATE_FILE = os.path.join(os.getcwd(), ".setup_state.json")

# ANSI colors for pretty output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

TESSERACT_OPT_OUT_FLAG_FILE = os.path.expanduser("~/.suna_skip_tesseract_check")

# --- State Persistence Functions ---
def load_state():
    """Loads the setup state from STATE_FILE."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                print_info(f"Loaded saved setup state from {STATE_FILE}")
                return state
        except (IOError, json.JSONDecodeError) as e:
            print_warning(f"Could not load or parse state file {STATE_FILE}: {e}. Starting with a fresh state.")
    return {}

def save_state(state):
    """Saves the setup state to STATE_FILE."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=4)
        # print_info(f"Saved setup state to {STATE_FILE}") # Can be too verbose if called often
    except IOError as e:
        print_warning(f"Could not save state file {STATE_FILE}: {e}")

def mask_api_key(api_key):
    """Masks an API key, showing first 4 and last 4 characters."""
    if not api_key or len(api_key) < 8:
        return "****"  # Too short to mask meaningfully
    return f"{api_key[:4]}****{api_key[-4:]}"

def mask_url(url):
    """Masks a URL, showing scheme and parts of the domain."""
    if not url:
        return ""
    try:
        protocol_end = url.find("://")
        if protocol_end == -1:
            # No protocol, try to mask based on domain-like structure
            parts = url.split('.')
            if len(parts) > 1 and len(parts[-2]) > 3: # e.g. domain.com
                 return f"{parts[-2][:2]}****.{parts[-1]}"
            return "****" # Cannot mask meaningfully
            
        protocol = url[:protocol_end+3]
        domain_part = url[protocol_end+3:]
        
        domain_parts = domain_part.split('.')
        if len(domain_parts) > 1: # e.g. abcdefg.supabase.co or localhost:3000
            host = domain_parts[0]
            rest = '.'.join(domain_parts[1:])
            if len(host) > 6:
                masked_host = f"{host[:3]}****{host[-3:]}"
            else:
                masked_host = f"{host[:1]}****"
            
            # Check for port
            port_match = re.search(r':\d+$', rest)
            port_str = ""
            if port_match:
                port_str = port_match.group(0)
                rest = rest[:port_match.start()]

            if rest: # If there are TLDs like .co, .io
                return f"{protocol}{masked_host}.{rest}{port_str}"
            else: # Likely just 'localhost' or similar without a TLD in domain_parts
                return f"{protocol}{masked_host}{port_str}"
        else: # Simple domain like 'localhost'
            if len(domain_part) > 4:
                return f"{protocol}{domain_part[:2]}****{domain_part[-2:]}"
            return f"{protocol}****"
            
    except Exception: # General catch-all if URL parsing is tricky
        return "****" # Fallback for complex or unexpected URL formats

# --- End State Persistence Functions ---


# Helper function to run winget install
def run_winget_install(package_id, package_name):
    """
    Attempts to install a package using winget.
    Returns a tuple: (success: bool, already_installed: bool)
    """
    if not IS_WINDOWS:
        return False, False

    try:
        subprocess.run(['winget', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
        print_info(f"winget is available. Attempting to install {package_name}...")
    except (subprocess.SubprocessError, FileNotFoundError):
        print_warning("winget command not found. Please install winget or install the tool manually.")
        return False, False

    already_installed_flag = False

    def _check_install_status(process_result, pkg_name):
        nonlocal already_installed_flag
        stdout = process_result.stdout.lower() if process_result.stdout else ""
        stderr = process_result.stderr.lower() if process_result.stderr else ""

        # Winget error codes: https://learn.microsoft.com/en-us/windows/package-manager/winget/return-codes
        # 0x8A15002B / 2316632107 / -2011873237 (SCHED_E_TASK_TERMINATED) - sometimes means already installed or needs elevation
        # 0x8A150018 / 2316632088 / -2011873264 (INSTALL_PACKAGE_IN_USE)
        # Common strings indicating already installed (these can vary by winget version and locale)
        already_installed_strings = [
            "already installed",  # English
            "package already installed", # English
            "no applicable upgrade found", # English
            "no upgrade available", # English
            "found an existing package if this is not the intended application", # More verbose
            # Add other language strings if necessary, e.g.:
            # "ya está instalado", # Spanish
            # "déjà installé", # French
        ]
        # Specific error codes that might imply "already installed" or "no action needed"
        # WINGET_ERROR_ALREADY_INSTALLED (0x8A150057) is not directly listed in public docs but observed.
        # Some sources suggest 0x80070000 related codes.
        # For Python specifically, "Python 3.11.X is already installed."
        python_specific_already_installed = f"{pkg_name.lower()} is already installed" # More specific for Python

        if python_specific_already_installed in stdout or python_specific_already_installed in stderr:
            print_info(f"{pkg_name} is already installed (detected by specific message).")
            already_installed_flag = True
            return True # Treat as success

        for s in already_installed_strings:
            if s in stdout or s in stderr:
                print_info(f"{pkg_name} is already installed or no upgrade needed (detected by string: '{s}').")
                already_installed_flag = True
                return True # Treat as success

        # Check for specific return codes that indicate "already installed" or similar non-failure states
        # NoApplicableUpgrade 0x8A15010E / 2316632334
        if process_result.returncode == 0x8A15010E: # NoApplicableUpgrade
            print_info(f"{pkg_name} has no applicable upgrade. Assuming already installed or latest.")
            already_installed_flag = True
            return True

        # If return code is 0, it's a success regardless of "already installed" strings
        if process_result.returncode == 0:
            print_info(f"winget install command output for {pkg_name}:\n{process_result.stdout}")
            print_success(f"{pkg_name} installation via winget successful.")
            return True

        # Handle specific error codes before generic failure
        if process_result.returncode == 2316632107: # 0x8A15002B (SCHED_E_TASK_TERMINATED)
            print_warning(f"Winget returned exit code 2316632107 (SCHED_E_TASK_TERMINATED) for {pkg_name}.")
            print_warning("This can sometimes mean the package is already installed, or it might indicate an issue.")
            print_warning("Consider it a potential pre-existing installation. If issues persist, manual check is advised.")
            # We might heuristically decide this means already_installed if other clues exist,
            # but for now, let's not set already_installed_flag unless a string confirms it.
            # Let's return True to allow the script to proceed, but not mark as 'already_installed' unless a string confirms.
            # This is a tricky case. If this code means "installed" for Python, we need to catch it.
            # However, if it means "failed", we should return False.
            # Given the ambiguity, let's be conservative for now and not assume it means "already installed"
            # unless a string also indicates it.
            # If Python install fails with this and no string, then the outer logic will catch it.
            # For Python, if winget says "Python 3.11.X is already installed" AND this code, it's fine.
            # If it's just this code, it's ambiguous.
            # Let's assume if this code appears, it's a success (as in, no need to retry with different flags for now)
            # but *not* necessarily "already_installed".
            # The function will return (True, already_installed_flag)
            return True # Tentative success, let the caller decide based on `already_installed_flag`

        # Generic failure
        print_error(f"winget installation of {pkg_name} failed with exit code {process_result.returncode}.")
        if process_result.stdout:
            print_error(f"Winget stdout:\n{process_result.stdout}")
        if process_result.stderr:
            print_error(f"Winget stderr:\n{process_result.stderr}")
        return False


    try:
        install_command = [
            'winget', 'install', package_id,
            '-s', 'winget',
            '--accept-package-agreements',
            '--accept-source-agreements',
            '--disable-interactivity'
        ]
        print_info(f"Executing winget command: {' '.join(install_command)}")
        process = subprocess.run(install_command, capture_output=True, text=True, check=False, shell=True)

        if _check_install_status(process, package_name):
            return True, already_installed_flag
        else:
            # If initial attempt failed and wasn't due to "already installed"
            # Check for access denied, which is a common issue for winget
            stderr_lower = process.stderr.lower() if process.stderr else ""
            if "0x80070005" in stderr_lower or "access is denied" in stderr_lower or "error 0x80070005" in stderr_lower:
                print_warning("Winget may require administrator privileges. The first attempt failed with an access denied error.")
                print_warning("Please try running this script in an administrator terminal if issues persist.")
            
            # Fallback: Try without --disable-interactivity as it sometimes helps
            # This is only if the first attempt truly failed (not already installed)
            print_info(f"Retrying winget install for {package_name} without --disable-interactivity...")
            install_command_fallback = [
                'winget', 'install', package_id,
                '-s', 'winget',
                '--accept-package-agreements',
                '--accept-source-agreements'
            ]
            print_info(f"Executing winget command (fallback): {' '.join(install_command_fallback)}")
            process_fallback = subprocess.run(install_command_fallback, capture_output=True, text=True, check=False, shell=True)

            if _check_install_status(process_fallback, package_name):
                return True, already_installed_flag # already_installed_flag would be set by _check_install_status
            else:
                # If fallback also failed
                print_error(f"Fallback winget install for {package_name} also failed.")
                return False, False # Definitely failed

    except Exception as e: # Catches other unexpected errors like if winget itself is not runnable after the initial check
        print_error(f"An unexpected error occurred during winget installation of {package_name}: {e}")
        return False, False

def print_banner():
    """Print Suna setup banner"""
    print(f"""
{Colors.BLUE}{Colors.BOLD}
   ███████╗██╗   ██╗███╗   ██╗ █████╗ 
   ██╔════╝██║   ██║████╗  ██║██╔══██╗
   ███████╗██║   ██║██╔██╗ ██║███████║
   ╚════██║██║   ██║██║╚██╗██║██╔══██║
   ███████║╚██████╔╝██║ ╚████║██║  ██║
   ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝
                                      
   Setup Wizard
{Colors.ENDC}
""")

def print_step(step_num, total_steps, step_name):
    """Print a step header"""
    print(f"\n{Colors.BLUE}{Colors.BOLD}Step {step_num}/{total_steps}: {step_name}{Colors.ENDC}")
    print(f"{Colors.CYAN}{'='*50}{Colors.ENDC}\n")

def print_info(message):
    """Print info message"""
    print(f"{Colors.CYAN}ℹ️  {message}{Colors.ENDC}")

def print_success(message):
    """Print success message"""
    print(f"{Colors.GREEN}✅  {message}{Colors.ENDC}")

def print_warning(message):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠️  {message}{Colors.ENDC}")

def print_error(message):
    """Print error message"""
    print(f"{Colors.RED}❌  {message}{Colors.ENDC}")

def check_requirements():
    """Check if all required tools are installed"""
    requirements = {
        # Tool name: (URL, winget_package_id, winget_package_name, specific_version_check_command (optional))
        'git': ('https://git-scm.com/downloads', 'Git.Git', 'Git', None),
        # Python 3.11 check is now primarily handled by ensure_python_311_and_venv()
        # However, we keep an entry here to ensure 'python' (from venv) is reported as found.
        'python3': ('https://www.python.org/downloads/', 'Python.Python.3.11', 'Python 3.11', [sys.executable, '--version']),
        'pip3': ('https://pip.pypa.io/en/stable/installation/', None, 'pip3', None), # pip should come with python
        'node': ('https://nodejs.org/en/download/', 'OpenJS.NodeJS', 'Node.js (includes npm)', None), # Node includes npm
        'npm': ('https://docs.npmjs.com/downloading-and-installing-node-js-and-npm', None, 'npm', None), # npm check, but installed with Node
        'poetry': ('https://python-poetry.org/docs/#installation', 'PythonPoetry.Poetry', 'Poetry', None),
        'docker': ('https://docs.docker.com/get-docker/', None, 'Docker', None), # Docker handled separately
        'tesseract': ('https://github.com/UB-Mannheim/tesseract/wiki', 'UB-Mannheim.TesseractOCR', 'Tesseract OCR', None),
    }
    
    missing = []
    installed_via_winget_needs_path_check = []

    # Tesseract opt-out flag file is defined globally as TESSERACT_OPT_OUT_FLAG_FILE

    # Flags specifically for Tesseract detection
    tesseract_found_in_path = False
    tesseract_found_by_alternative_method = False
    # tesseract_verified_path = None # To store the full path if found by alternative method

    for cmd, details in requirements.items():
        url, winget_id, winget_name, specific_version_check = details
        cmd_to_check = cmd.replace('3', '') if IS_WINDOWS and cmd in ['python3', 'pip3'] else cmd

        if cmd == 'tesseract':
            if os.path.exists(TESSERACT_OPT_OUT_FLAG_FILE):
                print_info(f"Tesseract OCR check/installation is skipped due to user opt-out flag: {TESSERACT_OPT_OUT_FLAG_FILE}")
                continue # Skip all processing for Tesseract
            
            # Reset flags for each check iteration (though Tesseract is only checked once)
            tesseract_found_in_path = False
            tesseract_found_by_alternative_method = False
            # tesseract_verified_path = None 

        try:
            # Initial check for Tesseract in PATH (will be silent fail for tesseract)
            if cmd == 'tesseract':
                try:
                    subprocess.run([cmd_to_check, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                    print_success(f"{cmd} is installed (found in PATH)")
                    tesseract_found_in_path = True
                    continue # Found in PATH, move to next requirement
                except (subprocess.SubprocessError, FileNotFoundError):
                    # Do not print error yet, will be handled by later logic if not found by other means
                    tesseract_found_in_path = False # Explicitly set
                    # Fall through to advanced detection or general error handling

            # If we are in the venv, python3 check should use sys.executable and is implicitly 3.11
            elif cmd == 'python3' and os.environ.get(VENV_ACTIVATION_MARKER) == "1": # Use elif to avoid re-checking tesseract here
                current_python_version = get_python_version(sys.executable)
                if current_python_version and "3.11" in current_python_version:
                    print_success(f"Python 3.11 ({current_python_version}) is active in the virtual environment.")
                else:
                    # This case should ideally not be reached if ensure_python_311_and_venv worked correctly.
                    print_error(f"Inside venv, but Python version is {current_python_version} (expected 3.11).")
                    missing.append((cmd, url)) # Add to missing to indicate a problem
                    continue # Next requirement
            elif cmd == 'python3': # Not in venv, or marker not set (should have been handled by ensure_python_311_and_venv)
                 # This path should ideally not be hit for python3 if ensure_python_311_and_venv is called first.
                 # If it is, it means ensure_python_311_and_venv didn't run or didn't exit upon failure.
                py_version = get_python_version('python')
                if py_version and "3.11" in py_version:
                    print_success(f"Python 3.11 ({py_version}) found in PATH.")
                elif IS_WINDOWS:
                    py_version_launcher = get_python_version('py -3.11')
                    if py_version_launcher and "3.11" in py_version_launcher:
                         print_success(f"Python 3.11 ({py_version_launcher}) found via 'py -3.11'.")
                    else:
                        # This will be caught by the FileNotFoundError below and attempt winget if applicable
                        raise FileNotFoundError("Python 3.11 not found via 'python' or 'py -3.11'")
                else: # Non-windows
                    raise FileNotFoundError("Python 3.11 not found in PATH")


            # Standard check for other tools (not Tesseract if already handled or path check failed)
            # or if python3 check above passed through
            if not (cmd == 'tesseract' and not tesseract_found_in_path): # Skip this for Tesseract if initial PATH check failed
                version_check_cmd = [cmd_to_check, '--version']
                if specific_version_check: # e.g. for python3, this now uses sys.executable if in venv
                    subprocess.run(specific_version_check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                else:
                    subprocess.run(version_check_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                
                print_success(f"{cmd} is installed")

            # If node is installed, assume npm is too, but verify npm separately if it's its own entry.
            if cmd == 'node' and 'npm' in requirements and not any(m[0] == 'npm' for m in missing) and not tesseract_found_in_path: # ensure not tesseract
                try:
                    subprocess.run(['npm', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                    print_success("npm is installed (comes with Node.js)")
                except (subprocess.SubprocessError, FileNotFoundError):
                    print_error("npm is not found, though Node.js seems installed. This is unexpected.")
                    if IS_WINDOWS:
                        print_info("Node.js installer should include npm. A PATH issue or incomplete installation might be the cause.")
                    missing.append(('npm', requirements['npm'][0]))
        
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            # Only print initial error if it's not Tesseract failing the PATH check
            if not (cmd == 'tesseract' and not tesseract_found_in_path):
                original_error_msg = f"{cmd} is not installed or not found in PATH."
                if isinstance(e, subprocess.SubprocessError) and hasattr(e, 'stderr') and e.stderr:
                    original_error_msg += f" Error: {e.stderr.strip()}"
                elif isinstance(e, FileNotFoundError) and cmd == 'python3':
                    if os.environ.get(VENV_ACTIVATION_MARKER) == "1":
                        original_error_msg = f"Python 3.11 check failed using '{sys.executable}'. This is unexpected in the activated venv."
                    else:
                        original_error_msg = "Python 3.11 is required but not found."
                print_error(original_error_msg)

            if IS_WINDOWS and cmd == 'python3' and os.environ.get(VENV_ACTIVATION_MARKER) != "1":
                print_info(f"Attempting to install {winget_name} for Python 3.11 using winget as a fallback...")
                winget_success, winget_already_installed = run_winget_install(winget_id, winget_name)
                if winget_success:
                    if winget_already_installed:
                        print_info(f"{winget_name} was already installed (reported by winget). A new terminal might be needed.")
                    else:
                        print_success(f"{winget_name} installation via winget seems successful.")
                    print_info("Please re-run the setup script in a new terminal for changes to take effect.")
                    sys.exit(0)
                else:
                    print_error(f"Automated installation of {winget_name} via winget failed.")
                    print_info(f"Please install {cmd} manually from {url}")
                    print_info("Ensure 'Add Python to PATH' is checked during installation if applicable.")
                    missing.append((cmd, url))
            
            elif IS_WINDOWS and cmd == 'tesseract' and not tesseract_found_in_path and not tesseract_found_by_alternative_method:
                # Enhanced Tesseract detection for Windows - only if not found in PATH and not by other methods yet
                tesseract_exe_path_alt = None # Use a different variable name to avoid conflict
                detection_method_alt = None

                # 1. Check TESSDATA_PREFIX environment variable
                tessdata_prefix = os.environ.get('TESSDATA_PREFIX')
                if tessdata_prefix:
                    print_info(f"TESSDATA_PREFIX found: {tessdata_prefix}. Checking for Tesseract...")
                    potential_path = os.path.abspath(os.path.join(tessdata_prefix, '..'))
                    search_paths_tess = [potential_path, os.path.join(potential_path, 'bin')]
                    for path_tess in search_paths_tess:
                        test_exe_tess = os.path.join(path_tess, 'tesseract.exe')
                        if os.path.isfile(test_exe_tess):
                            try:
                                subprocess.run([test_exe_tess, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                                tesseract_exe_path_alt = test_exe_tess
                                detection_method_alt = f"TESSDATA_PREFIX environment variable ({tessdata_prefix})"
                                break
                            except (subprocess.SubprocessError, FileNotFoundError): pass
                    if tesseract_exe_path_alt:
                        print_success(f"Tesseract is installed (verified via {detection_method_alt} at {tesseract_exe_path_alt})")
                        requirements[cmd] = (url, winget_id, winget_name, [tesseract_exe_path_alt, '--version'])
                        tesseract_found_by_alternative_method = True
                        continue

                # 2. Check common installation paths
                if not tesseract_found_by_alternative_method: # Check if already found by TESSDATA_PREFIX
                    common_paths_tess = [
                        os.path.join(os.environ.get('ProgramFiles', 'C:\\Program Files'), 'Tesseract-OCR'),
                        os.path.join(os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)'), 'Tesseract-OCR')
                    ]
                    for base_path_tess in common_paths_tess:
                        search_paths_common = [base_path_tess, os.path.join(base_path_tess, 'bin')]
                        for path_common in search_paths_common:
                            test_exe_common = os.path.join(path_common, 'tesseract.exe')
                            if os.path.isfile(test_exe_common):
                                try:
                                    subprocess.run([test_exe_common, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                                    tesseract_exe_path_alt = test_exe_common
                                    detection_method_alt = f"common installation path ({path_common})"
                                    break
                                except (subprocess.SubprocessError, FileNotFoundError): pass
                        if tesseract_exe_path_alt: break 
                    if tesseract_exe_path_alt:
                        print_success(f"Tesseract is installed (verified via {detection_method_alt} at {tesseract_exe_path_alt})")
                        requirements[cmd] = (url, winget_id, winget_name, [tesseract_exe_path_alt, '--version'])
                        tesseract_found_by_alternative_method = True
                        continue

                # 3. Check Windows Registry
                if not tesseract_found_by_alternative_method: # Check if already found by common paths
                    registry_keys_tess = [
                        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\tesseract.exe', ''),
                        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\DigiObjects\TesseractOCR', 'Path'), # Older Tesseract versions
                        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\DigiObjects\TesseractOCR', 'InstallationPath'),
                        (winreg.HKEY_CURRENT_USER, r'SOFTWARE\DigiObjects\TesseractOCR', 'Path'),
                        (winreg.HKEY_CURRENT_USER, r'SOFTWARE\DigiObjects\TesseractOCR', 'InstallationPath'),
                        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Tesseract-OCR', 'Path'), # Newer Tesseract versions
                        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Tesseract-OCR', 'InstallationPath'),
                        (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Tesseract-OCR', 'Path'),
                        (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Tesseract-OCR', 'InstallationPath'),
                    ]
                    for hive, key_path_reg, value_name_reg in registry_keys_tess:
                        try:
                            with winreg.OpenKey(hive, key_path_reg) as key_reg:
                                reg_path_val, _ = winreg.QueryValueEx(key_reg, value_name_reg)
                                if reg_path_val:
                                    potential_exe_reg = reg_path_val if not value_name_reg else os.path.join(reg_path_val, 'tesseract.exe')
                                    test_exe_reg = None
                                    if os.path.isfile(potential_exe_reg): test_exe_reg = potential_exe_reg
                                    elif value_name_reg and os.path.isfile(os.path.join(reg_path_val, 'bin', 'tesseract.exe')):
                                        test_exe_reg = os.path.join(reg_path_val, 'bin', 'tesseract.exe')
                                    
                                    if test_exe_reg:
                                        print_info(f"Testing Tesseract from registry: {test_exe_reg} (Key: {key_path_reg}\\{value_name_reg})")
                                        try:
                                            subprocess.run([test_exe_reg, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                                            tesseract_exe_path_alt = test_exe_reg
                                            detection_method_alt = f"Windows Registry ({key_path_reg}\\{value_name_reg})"
                                            break
                                        except (subprocess.SubprocessError, FileNotFoundError):
                                            print_warning(f"Found Tesseract via registry at {test_exe_reg}, but '--version' check failed.")
                        except FileNotFoundError: pass
                        except OSError as oe_reg: print_warning(f"Error accessing registry key {key_path_reg}: {oe_reg}")
                        if tesseract_exe_path_alt: break
                    if tesseract_exe_path_alt:
                        print_success(f"Tesseract is installed (verified via {detection_method_alt} at {tesseract_exe_path_alt})")
                        requirements[cmd] = (url, winget_id, winget_name, [tesseract_exe_path_alt, '--version'])
                        tesseract_found_by_alternative_method = True
                        continue
                
                # If Tesseract still not found by specific custom methods, try Chocolatey then Winget
                # This block is reached if cmd == 'tesseract', IS_WINDOWS, not tesseract_found_in_path, not tesseract_found_by_alternative_method
                
                # Try Chocolatey for Tesseract
                try:
                    subprocess.run(['choco', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                    print_info("Chocolatey found. Attempting to install Tesseract OCR using Chocolatey...")
                    choco_install_command = ['choco', 'install', 'tesseract-ocr', '-y', '--params', '"/InstallDir=C:\\Program Files\\Tesseract-OCR /Path"']
                    process_choco = subprocess.run(choco_install_command, capture_output=True, text=True, shell=True)
                    if process_choco.returncode == 0:
                        print_success("Tesseract OCR installation via Chocolatey seems successful.")
                        print_info(f"Chocolatey output:\n{process_choco.stdout}")
                        installed_via_winget_needs_path_check.append((cmd, url, cmd_to_check))
                        tesseract_found_by_alternative_method = True # Mark as found for now, re-check will verify
                        continue 
                    else:
                        print_error(f"Chocolatey install of Tesseract OCR failed. Exit code: {process_choco.returncode}")
                        if process_choco.stdout: print_error(f"Choco stdout:\n{process_choco.stdout}")
                        if process_choco.stderr: print_error(f"Choco stderr:\n{process_choco.stderr}")
                except (subprocess.SubprocessError, FileNotFoundError) as choco_e:
                    if isinstance(choco_e, FileNotFoundError): print_info("Chocolatey (choco) not found. Skipping Chocolatey for Tesseract.")
                    else: print_warning(f"Error during Chocolatey check or install for Tesseract: {choco_e}.")

                # If not found by Choco or Choco failed/not available, try Winget for Tesseract
                if not tesseract_found_by_alternative_method and winget_id : # winget_id for tesseract: UB-Mannheim.TesseractOCR
                    print_info(f"Attempting to install {winget_name} using winget...")
                    winget_success_tess, _ = run_winget_install(winget_id, winget_name)
                    if winget_success_tess:
                        installed_via_winget_needs_path_check.append((cmd, url, cmd_to_check))
                        tesseract_found_by_alternative_method = True # Mark as found, re-check will verify
                        continue
                    # If winget also fails, it will fall through to the generic tesseract error message block
            
            # General handling for tools other than Tesseract if IS_WINDOWS, or if Tesseract checks above didn't `continue`
            # This 'elif IS_WINDOWS:' ensures this block is not entered if Tesseract specific logic for Windows already handled it and continued.
            elif IS_WINDOWS: 
                if cmd == 'docker':
                    print_error("Docker installation cannot be automated by this script.")
                    print_info("Please install Docker Desktop for Windows manually.")
                    print_info("  - Download from: https://www.docker.com/products/docker-desktop/")
                    print_info("  - Ensure WSL2 (Windows Subsystem for Linux 2) is enabled.")
                    print_info("    To enable WSL2, open PowerShell as Administrator and run: ")
                    print_info("    dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart")
                    print_info("    dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart")
                    print_info("    Then restart your computer and set WSL2 as default: wsl --set-default-version 2")
                    print_info("  - Enable hardware virtualization (VT-x or AMD-V) in your computer's BIOS/UEFI settings.")
                    print_info("After installing and starting Docker Desktop, please re-run this setup script.")
                    missing.append((cmd, url))
                elif winget_id:
                    print_info(f"Attempting to install {winget_name} using winget...")
                    if cmd == 'poetry': # Poetry special handling
                        # First, try pip install if python (from venv, so it's python 3.11) is available
                        try:
                            # Use sys.executable to ensure pip is called from the venv's Python
                            pip_install_cmd = [sys.executable, '-m', 'pip', 'install', 'poetry']
                            print_info(f"Attempting to install Poetry using: {' '.join(pip_install_cmd)}")
                            subprocess.run(pip_install_cmd, check=True, shell=False)
                            print_success("Poetry installed successfully using pip in the current environment.")
                            
                            # Verify the pip-installed Poetry using its direct path in the venv
                            poetry_in_venv_scripts = os.path.join(VENV_PATH, 'Scripts', 'poetry.exe')
                            print_info(f"Verifying Poetry at {poetry_in_venv_scripts}...")
                            # This subprocess.run will raise SubprocessError if it fails (due to check=True)
                            # and be caught by the outer `except subprocess.SubprocessError as poetry_pip_e`
                            subprocess.run([poetry_in_venv_scripts, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=False)
                            print_success(f"Poetry successfully verified at {poetry_in_venv_scripts} ('--version').")
                            continue # Successfully installed and verified via pip, skip to next requirement

                        except subprocess.SubprocessError as poetry_pip_e:
                            # This block catches failures from 'pip install poetry' OR from the verification of 'poetry.exe --version'
                            print_warning(f"Poetry installation via pip or its verification failed: {poetry_pip_e}. Attempting winget install for Poetry...")
                            winget_success_poetry, _ = run_winget_install(winget_id, winget_name) # already_installed doesn't matter as much here
                            if winget_success_poetry:
                                installed_via_winget_needs_path_check.append((cmd, url, cmd_to_check))
                            else:
                                print_error(f"Automated installation of {winget_name} via pip and winget also failed.")
                                print_info(f"Please install {cmd} manually from {url}")
                                print_info("For Poetry, the recommended method is often via pip or their official install script.")
                                missing.append((cmd, url))
                    else: # For other tools (not poetry, not python3 handled by ensure_python_311_and_venv)
                        winget_success_other, _ = run_winget_install(winget_id, winget_name)
                        if winget_success_other:
                            installed_via_winget_needs_path_check.append((cmd, url, cmd_to_check))
                        else: # All automated attempts for this tool (including winget) failed
                            if cmd == 'tesseract':
                                print_warning("All automated attempts to install Tesseract OCR have failed (PATH, common locations, registry, Chocolatey, winget).")
                                user_choice = input(f"{Colors.YELLOW}Would you like to skip Tesseract OCR requirement for now and in future Suna setups? (yes/no): {Colors.ENDC}").strip().lower()
                                if user_choice in ['yes', 'y']:
                                    try:
                                        with open(TESSERACT_OPT_OUT_FLAG_FILE, 'w') as f:
                                            f.write("User opted out of Tesseract OCR check.")
                                        print_info(f"Tesseract OCR requirement will be skipped in future runs. Flag file created at: {TESSERACT_OPT_OUT_FLAG_FILE}")
                                        continue # Skip adding to missing list and manual instructions for this run
                                    except IOError as e_io:
                                        print_error(f"Could not create opt-out flag file at {TESSERACT_OPT_OUT_FLAG_FILE}: {e_io}")
                                        print_info("Proceeding with manual installation instructions for Tesseract.")
                                # If user says no, or if flag creation failed, proceed to print manual instructions and add to missing list.
                            
                            print_error(f"Automated installation of {winget_name} failed or winget is not available.")
                            print_info(f"Please install {cmd} manually from {url}")
                        if cmd == 'python3':
                             print_info("Download the Python 3.11 installer from the URL and run it. Ensure 'Add Python to PATH' is checked during installation.")
                        elif cmd == 'git':
                             print_info("Download the Git installer from the URL and run it, accepting default options is usually fine.")
                        elif cmd == 'node':
                             print_info("Download the Node.js LTS installer from the URL and run it. This will also install npm.")
                        elif cmd == 'tesseract': 
                            # This block is now reached if:
                            # 1. All automated attempts failed (PATH, custom, choco, winget)
                            # 2. AND the user chose NOT to opt-out, or opt-out file creation failed.
                            print_error("Tesseract OCR is not installed or not found in PATH. This is required for some features unless opted out.")
                            print_info("Please install it manually:")
                            print_info("1. Download the installer from: https://github.com/UB-Mannheim/tesseract/wiki (look for Windows installers).")
                            print_info("2. Run the installer.")
                            print_info("3. Important: During installation, ensure you select the option to 'Add Tesseract to system PATH'. This might be under a component like 'Full installation' or a specific checkbox for PATH.")
                            print_info("   Alternatively, you can add it manually. Default path is often 'C:\\Program Files\\Tesseract-OCR'.")
                            print_info("4. After installation, you MUST restart this script or open a new terminal window for the PATH changes to take effect.")
                        missing.append((cmd, url))
                elif cmd == 'pip3' and any(m[0] == 'python3' for m in missing): # If Python failed, pip will also fail.
                    print_info("pip3 installation depends on Python. Python is not yet installed.")
                    missing.append((cmd,url))
                elif cmd == 'npm' and any(m[0] == 'node' for m in missing):
                     print_info("npm installation depends on Node.js. Node.js is not yet installed.")
                     missing.append((cmd,url))
                # elif cmd == 'tesseract': # Already handled by specific tesseract message above for failed winget
                #    pass # This is now handled by the more specific message above
                else: # No winget ID for this tool, or not docker
                    print_info(f"No automated Windows installation configured for {cmd}. Please install manually from {url}.")
                    missing.append((cmd, url))
            else: # Not windows
                if cmd == 'docker':
                     print_info(f"For Docker on non-Windows, please follow instructions at {url}")
                elif cmd == 'tesseract': # This is for non-Windows systems
                    print_warning("Tesseract OCR installation via system package manager or PATH check failed on this non-Windows system.")
                    user_choice = input(f"{Colors.YELLOW}Would you like to skip Tesseract OCR requirement for now and in future Suna setups? (yes/no): {Colors.ENDC}").strip().lower()
                    if user_choice in ['yes', 'y']:
                        try:
                            with open(TESSERACT_OPT_OUT_FLAG_FILE, 'w') as f:
                                f.write("User opted out of Tesseract OCR check.")
                            print_info(f"Tesseract OCR requirement will be skipped in future runs. Flag file created at: {TESSERACT_OPT_OUT_FLAG_FILE}")
                            continue # Skip adding to missing list and manual instructions
                        except IOError as e_io:
                            print_error(f"Could not create opt-out flag file at {TESSERACT_OPT_OUT_FLAG_FILE}: {e_io}")
                            print_info("Proceeding with manual installation instructions for Tesseract.")
                    
                    # If user did not opt-out or flag creation failed:
                    print_error("Tesseract OCR is not installed or not found in PATH.")
                    print_info("Please install Tesseract OCR for your OS from: https://github.com/UB-Mannheim/tesseract/wiki")
                    print_info("Ensure it's added to your system PATH.")
                    missing.append((cmd, url))
                else: # Other non-Windows tools that were not found
                    missing.append((cmd, url))

    # Re-check tools that were installed via package managers, as PATH might not have updated immediately
    if installed_via_winget_needs_path_check: # This list is used for Chocolatey installs too
        print_info("\nRe-checking tools installed via automated methods as PATH environment variable changes might require a new terminal session...")
        for cmd, url, cmd_to_check_again in installed_via_winget_needs_path_check:
            if cmd == 'tesseract' and os.path.exists(TESSERACT_OPT_OUT_FLAG_FILE):
                print_info(f"Skipping re-check for Tesseract due to opt-out flag: {TESSERACT_OPT_OUT_FLAG_FILE}")
                continue
            try:
                version_check_cmd = [cmd_to_check_again, '--version']
                if cmd == 'python3': # Re-check for Python (should be sys.executable if in venv)
                    # This re-check is mostly for consistency if winget installed it and a new shell was needed.
                    # If already in venv, this should pass easily.
                    py_version_recheck = get_python_version(sys.executable if os.environ.get(VENV_ACTIVATION_MARKER) == "1" else 'python')
                    if py_version_recheck and "3.11" in py_version_recheck:
                        print_success(f"Python 3.11 ({py_version_recheck}) successfully verified after potential installation.")
                    else:
                         raise FileNotFoundError(f"Python 3.11 not found or not the correct version ({py_version_recheck}) after automated install attempt.")
                elif cmd == 'poetry' and IS_WINDOWS: 
                    try:
                        # Try direct 'poetry --version' first
                        subprocess.run([cmd_to_check_again, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                        print_success(f"{cmd} successfully verified after installation ('{cmd_to_check_again} --version').")
                    except (subprocess.SubprocessError, FileNotFoundError):
                        # If direct call fails, try 'python -m poetry --version' as Poetry might be installed as a module
                        print_warning(f"'{cmd_to_check_again} --version' failed. Trying '{sys.executable} -m poetry --version'.")
                        try:
                            subprocess.run([sys.executable, '-m', 'poetry', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                            print_success(f"{cmd} successfully verified using '{sys.executable} -m poetry --version'.")
                        except (subprocess.SubprocessError, FileNotFoundError):
                            print_warning(f"{cmd} was reportedly installed (e.g., via pip or winget), but neither direct call nor '{sys.executable} -m poetry' works.")
                            print_info(f"This could be a PATH issue or incomplete installation.")
                            print_info(f"If installed via pip into a user script dir (e.g., %APPDATA%\\Python\\Python311\\Scripts on Windows), ensure that's in PATH.")
                            print_info(f"Please open a new terminal and re-run the setup. If the issue persists, manual PATH adjustment or reinstallation might be needed.")
                            missing.append((cmd,url))
                elif cmd == 'tesseract' and IS_WINDOWS:
                    try:
                        subprocess.run([cmd_to_check_again, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                        print_success(f"{cmd} successfully verified after automated installation.")
                    except (subprocess.SubprocessError, FileNotFoundError):
                        print_warning(f"{cmd} ('{cmd_to_check_again}') not immediately found in PATH after installation attempt.")
                        # Attempt temporary PATH modification for Tesseract
                        if cmd == 'tesseract':
                            default_tesseract_path = r"C:\Program Files\Tesseract-OCR"
                            if os.path.exists(default_tesseract_path):
                                print_info(f"Attempting to verify Tesseract by temporarily adding '{default_tesseract_path}' to PATH...")
                                temp_env = os.environ.copy()
                                temp_env['PATH'] = f"{default_tesseract_path}{os.pathsep}{temp_env.get('PATH', '')}"
                                try:
                                    subprocess.run([cmd_to_check_again, '--version'], env=temp_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                                    print_success(f"Tesseract successfully verified using temporary PATH modification with '{default_tesseract_path}'.")
                                    print_info("This indicates Tesseract is likely installed correctly. Please open a new terminal for the PATH changes to take full effect system-wide.")
                                    # If successful here, we don't add to missing list for Tesseract
                                    continue # Move to the next item in installed_via_winget_needs_path_check
                                except (subprocess.SubprocessError, FileNotFoundError):
                                    print_error(f"Tesseract verification still failed even after temporarily adding '{default_tesseract_path}' to PATH.")
                                    # Fall through to add to missing list below
                            else:
                                print_info(f"Default Tesseract installation path '{default_tesseract_path}' not found. Skipping temporary PATH modification.")
                        
                        # Original messaging if temporary PATH check wasn't done or also failed
                        print_error(f"{cmd} was reportedly installed by an automated method, but is still not found in PATH.")
                        print_info("This is often due to the PATH environment variable not being updated in the current terminal session.")
                        print_info(f"Please open a new terminal/command prompt and re-run this setup script.")
                        if cmd == 'tesseract':
                             tesseract_example_path = 'C:\\Program Files\\Tesseract-OCR'
                             print_info(f"If the problem persists, ensure Tesseract OCR's installation directory (e.g., '{default_tesseract_path or tesseract_example_path}') is in your system PATH.")
                        missing.append((cmd, url))
                else: # For other tools, not tesseract
                    subprocess.run(version_check_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
                    print_success(f"{cmd} successfully verified after automated installation.")
            except (subprocess.SubprocessError, FileNotFoundError) as e:
                final_error_msg = f"Verification of {cmd} failed after automated installation attempt."
                if isinstance(e, FileNotFoundError) and cmd == 'python3':
                    final_error_msg = f"Python 3.11 was reportedly installed, but '{sys.executable if os.environ.get(VENV_ACTIVATION_MARKER) == '1' else 'python'} --version' still fails or shows wrong version."

                print_error(final_error_msg)
                if isinstance(e, subprocess.SubprocessError) and hasattr(e, 'stderr') and e.stderr:
                    print_error(f"Error details: {e.stderr.strip()}")
                
                print_info("This could be due to the PATH environment variable not being updated in the current terminal session.")
                print_info(f"Please open a new terminal/command prompt and re-run this setup script.")
                print_info(f"If the problem persists, you may need to manually adjust your PATH or ensure the correct version is selected (e.g., using pyenv or similar tools for Python).")
                missing.append((cmd, url))
                
    # Filter out npm from missing if node is also missing, as node includes npm
    if any(m[0] == 'node' for m in missing):
        missing = [m for m in missing if m[0] != 'npm']
    # Filter out pip3 from missing if python3 is also missing
    if any(m[0] == 'python3' for m in missing):
        missing = [m for m in missing if m[0] != 'pip3']

    # Remove duplicates from missing list while preserving order (important for messages)
    seen_missing = set()
    unique_missing = []
    for item in missing:
        if item[0] not in seen_missing:
            unique_missing.append(item)
            seen_missing.add(item[0])
    missing = unique_missing

    if missing:
        print_error("\nMissing required tools or failed verification after automated install attempts.")
        print_info("Please install them manually based on the instructions above or ensure they are correctly added to your PATH, then re-run this script.")
        for cmd, url in missing:
            print(f"  - {cmd}: {url}")
        sys.exit(1)
    
    return True

def check_docker_running():
    """Check if Docker CLI is available and if Docker daemon is running, with retries."""
    # 1. Check if Docker command is even available (lightweight check)
    try:
        subprocess.run(['docker', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=IS_WINDOWS)
        print_info("Docker command found (Docker CLI seems installed).")
    except (subprocess.SubprocessError, FileNotFoundError):
        print_error("Docker command ('docker') not found in PATH.")
        print_info("Docker Desktop does not seem to be installed or its command-line tools are not in your system's PATH.")
        print_info("Please install Docker Desktop from: https://www.docker.com/products/docker-desktop/")
        if IS_WINDOWS:
            print_info("Ensure that WSL2 (Windows Subsystem for Linux 2) is enabled and that Docker Desktop is configured to use it.")
            print_info("You may need to restart your terminal or system after installation for PATH changes to take effect.")
        sys.exit(1)

    # 2. Check if Docker daemon is running using 'docker info', with retries
    MAX_RETRIES = 3  # Total attempts will be MAX_RETRIES + 1 (initial + retries)
    RETRY_DELAY_SECONDS = 15

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0: # This is a retry attempt
            print_info(f"Retrying Docker daemon check (attempt {attempt}/{MAX_RETRIES}). Waiting {RETRY_DELAY_SECONDS} seconds...")
            time.sleep(RETRY_DELAY_SECONDS)
        
        try:
            # Run 'docker info' to check daemon status
            # Use a timeout for 'docker info' as it can hang indefinitely if daemon is in a bad state
            docker_info_process = subprocess.run(
                ['docker', 'info'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                shell=IS_WINDOWS,
                timeout=30 # 30 seconds timeout for docker info command
            )
            # Log docker info output for debugging if needed, but keep it concise for user
            # print_info(f"Docker info stdout: {docker_info_process.stdout.decode()[:200]}...") # Example: Show first 200 chars
            print_success("Docker is installed and the Docker service/daemon is running.")
            return True
        except subprocess.TimeoutExpired:
            print_error("Checking Docker status ('docker info') timed out.")
            if attempt == 0:
                 print_warning("This could indicate the Docker daemon is stuck or very slow to respond.")
            # Fall through to the general SubprocessError handling for retry prompt
        except subprocess.SubprocessError as e:
            # This block catches errors from 'docker info' failing, implying daemon is not responding
            if attempt == 0: # First failure of 'docker info'
                print_warning("Docker command is available, but the Docker service/daemon doesn't seem to be responding correctly.")
                print_info("This usually means Docker Desktop is installed but not currently running, is still initializing, or has an issue.")
                print_info(f"Error details (if any): {e.stderr.decode().strip() if e.stderr else 'No specific error output.'}")
                print_info("Please ensure Docker Desktop is started and fully operational.")
                if IS_WINDOWS:
                    print_info("On Windows, ensure Docker Desktop is running and using the WSL2 backend if configured.")
            
            # If it's not the last attempt, prompt to retry or skip
            if attempt < MAX_RETRIES:
                try:
                    user_input = input(
                        f"{Colors.YELLOW}Press Enter to retry Docker check, or type 'skip' to abort setup: {Colors.ENDC}"
                    ).strip().lower()
                    if user_input == 'skip':
                        print_info("Docker check skipped by user. Aborting Suna setup.")
                        sys.exit(1)
                except KeyboardInterrupt:
                    print_info("\nSetup aborted by user during Docker check. Exiting.")
                    sys.exit(1)
            else: # All retries (including initial attempt) failed
                print_error("Docker service/daemon did not become responsive after multiple retries.")
                print_info("Please ensure Docker Desktop is installed correctly, running, and properly configured.")
                print_info("You might need to restart Docker Desktop, or your computer, and then try running this setup script again.")
                sys.exit(1)
                
    return False # Should technically be unreachable due to sys.exit(1) in the loop's else clause

def check_suna_directory():
    """Check if we're in a Suna repository"""
    required_dirs = ['backend', 'frontend']
    required_files = ['README.md', 'docker-compose.yaml']
    
    for directory in required_dirs:
        if not os.path.isdir(directory):
            print_error(f"'{directory}' directory not found. Make sure you're in the Suna repository root.")
            return False
    
    for file in required_files:
        if not os.path.isfile(file):
            print_error(f"'{file}' not found. Make sure you're in the Suna repository root.")
            return False
    
    print_success("Suna repository detected")
    return True

def validate_url(url, allow_empty=False):
    """Validate a URL"""
    if allow_empty and not url:
        return True
    
    pattern = re.compile(
        r'^(?:http|https)://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # domain
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # or IP
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    
    return bool(pattern.match(url))

def validate_api_key(api_key, allow_empty=False):
    """Validate an API key (basic format check)"""
    if allow_empty and not api_key:
        return True
    
    # Basic check: not empty and at least 10 chars
    return bool(api_key)

def collect_supabase_info(existing_info={}):
    """Collect Supabase information, allowing use of existing info if available."""
    if existing_info and \
       existing_info.get('SUPABASE_URL') and \
       existing_info.get('SUPABASE_ANON_KEY') and \
       existing_info.get('SUPABASE_SERVICE_ROLE_KEY'):
        
        print_info("Found existing Supabase configuration:")
        print_info(f"  Supabase Project URL: {mask_url(existing_info['SUPABASE_URL'])}")
        print_info(f"  Supabase anon key: {mask_api_key(existing_info['SUPABASE_ANON_KEY'])}")
        print_info(f"  Supabase service role key: {mask_api_key(existing_info['SUPABASE_SERVICE_ROLE_KEY'])}")
        
        use_existing = input(f"{Colors.YELLOW}Do you want to use this saved information? (yes/no, default: yes): {Colors.ENDC}").strip().lower()
        if use_existing in ['', 'yes', 'y']:
            print_info("Using saved Supabase information.")
            return existing_info
        else:
            print_info("Proceeding to collect new Supabase information.")

    print_info("You'll need to create a Supabase project before continuing")
    print_info("Visit https://supabase.com/dashboard/projects to create one")
    print_info("After creating your project, visit the project settings -> Data API and you'll need to get the following information:")
    print_info("1. Supabase Project URL (e.g., https://abcdefg.supabase.co)")
    print_info("2. Supabase anon key")
    print_info("3. Supabase service role key")
    input("Press Enter to continue once you've created your Supabase project...")
    
    while True:
        supabase_url = input("Enter your Supabase Project URL (e.g., https://abcdefg.supabase.co): ")
        if validate_url(supabase_url):
            break
        print_error("Invalid URL format. Please enter a valid URL.")
    
    while True:
        supabase_anon_key = input("Enter your Supabase anon key: ")
        if validate_api_key(supabase_anon_key):
            break
        print_error("Invalid API key format. It should be at least 10 characters long.")
    
    while True:
        supabase_service_role_key = input("Enter your Supabase service role key: ")
        if validate_api_key(supabase_service_role_key):
            break
        print_error("Invalid API key format. It should be at least 10 characters long.")
    
    return {
        'SUPABASE_URL': supabase_url,
        'SUPABASE_ANON_KEY': supabase_anon_key,
        'SUPABASE_SERVICE_ROLE_KEY': supabase_service_role_key,
    }

def collect_daytona_info(existing_info={}):
    """Collect Daytona API key, allowing use of existing info if available."""
    if existing_info and existing_info.get('DAYTONA_API_KEY'):
        print_info("Found existing Daytona configuration:")
        print_info(f"  Daytona API Key: {mask_api_key(existing_info['DAYTONA_API_KEY'])}")
        
        use_existing = input(f"{Colors.YELLOW}Do you want to use this saved API key? (yes/no, default: yes): {Colors.ENDC}").strip().lower()
        if use_existing in ['', 'yes', 'y']:
            print_info("Using saved Daytona API key.")
            # Ensure all expected keys are present, even if just defaults from original function
            return {
                'DAYTONA_API_KEY': existing_info['DAYTONA_API_KEY'],
                'DAYTONA_SERVER_URL': existing_info.get('DAYTONA_SERVER_URL', "https://app.daytona.io/api"),
                'DAYTONA_TARGET': existing_info.get('DAYTONA_TARGET', "us"),
            }
        else:
            print_info("Proceeding to collect new Daytona API key.")

    print_info("You'll need to create a Daytona account before continuing")
    print_info("Visit https://app.daytona.io/ to create one")
    print_info("Then, generate an API key from 'Keys' menu")
    print_info("After that, go to Images (https://app.daytona.io/dashboard/images)")
    print_info("Click '+ Create Image'")
    print_info(f"Enter 'kortix/suna:0.1.2.8' as the image name")
    print_info(f"Set '/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf' as the Entrypoint")

    input("Press Enter to continue once you've completed these steps...")
    
    while True:
        daytona_api_key = input("Enter your Daytona API key: ")
        if validate_api_key(daytona_api_key):
            break
        print_error("Invalid API key format. It should be at least 10 characters long.")
    
    return {
        'DAYTONA_API_KEY': daytona_api_key,
        'DAYTONA_SERVER_URL': "https://app.daytona.io/api",
        'DAYTONA_TARGET': "us",
    }

def collect_llm_api_keys(existing_info={}):
    """Collect LLM API keys, allowing use of existing configuration if available."""
    has_existing_llm_config = False
    if existing_info:
        providers_configured = []
        if existing_info.get('OPENAI_API_KEY'):
            providers_configured.append(f"OpenAI (Key: {mask_api_key(existing_info['OPENAI_API_KEY'])})")
        if existing_info.get('ANTHROPIC_API_KEY'):
            providers_configured.append(f"Anthropic (Key: {mask_api_key(existing_info['ANTHROPIC_API_KEY'])})")
        if existing_info.get('OPENROUTER_API_KEY'):
            providers_configured.append(f"OpenRouter (Key: {mask_api_key(existing_info['OPENROUTER_API_KEY'])})")
        if existing_info.get('OLLAMA_API_BASE'):
            providers_configured.append(f"Ollama (Base URL: {existing_info['OLLAMA_API_BASE']})")
        
        default_model = existing_info.get('MODEL_TO_USE')

        if providers_configured or default_model:
            has_existing_llm_config = True
            print_info("Found existing LLM configuration:")
            if providers_configured:
                for p_info in providers_configured:
                    print_info(f"  - {p_info}")
            if default_model:
                print_info(f"  Default Model: {default_model}")
            else:
                print_info("  Default Model: Not set")

            use_existing = input(f"{Colors.YELLOW}Do you want to use this saved LLM configuration? (yes/no, default: yes): {Colors.ENDC}").strip().lower()
            if use_existing in ['', 'yes', 'y']:
                print_info("Using saved LLM configuration.")
                return existing_info # Return all of existing_info as it might contain other relevant keys like OR_SITE_URL
            else:
                print_info("Proceeding to re-configure LLM API keys.")
    
    print_info("You need at least one LLM provider API key to use Suna")
    print_info("Available LLM providers: OpenAI, Anthropic, OpenRouter")
    
    # Display provider selection options
    print(f"\n{Colors.CYAN}Select LLM providers to configure:{Colors.ENDC}")
    print(f"{Colors.CYAN}[1] {Colors.GREEN}OpenAI{Colors.ENDC}")
    print(f"{Colors.CYAN}[2] {Colors.GREEN}Anthropic{Colors.ENDC}")
    print(f"{Colors.CYAN}[3] {Colors.GREEN}OpenRouter{Colors.ENDC} {Colors.CYAN}(access to multiple models){Colors.ENDC}")
    print(f"{Colors.CYAN}[4] {Colors.GREEN}Ollama{Colors.ENDC} {Colors.CYAN}(local models, ensure Ollama server is running){Colors.ENDC}")
    print(f"{Colors.CYAN}Enter numbers separated by commas (e.g., 1,2,3,4){Colors.ENDC}\n")

    while True:
        providers_input = input("Select providers (required, at least one): ")
        selected_providers = []
        
        try:
            # Parse the input, handle both comma-separated and space-separated
            provider_numbers = [int(p.strip()) for p in providers_input.replace(',', ' ').split()]
            
            for num in provider_numbers:
                if num == 1:
                    selected_providers.append('OPENAI')
                elif num == 2:
                    selected_providers.append('ANTHROPIC')
                elif num == 3:
                    selected_providers.append('OPENROUTER')
                elif num == 4:
                    selected_providers.append('OLLAMA')
            
            if selected_providers:
                break
            else:
                print_error("Please select at least one provider.")
        except ValueError:
            print_error("Invalid input. Please enter provider numbers (e.g., 1,2,3).")

    # Collect API keys for selected providers
    api_keys = {}
    model_info = {}
    
    # Model aliases for reference
    model_aliases = {
        'OPENAI': ['openai/gpt-4o', 'openai/gpt-4o-mini'],
        'ANTHROPIC': ['anthropic/claude-3-7-sonnet-latest', 'anthropic/claude-3-5-sonnet-latest'],
        'OPENROUTER': ['openrouter/google/gemini-2.5-pro-preview', 'openrouter/deepseek/deepseek-chat-v3-0324:free', 'openrouter/openai/gpt-4o-2024-11-20'],
        'OLLAMA': ['ollama/llama3', 'ollama/llama2', 'ollama/codellama', 'ollama/mistral'],
    }
    
    for provider in selected_providers:
        print_info(f"\nConfiguring {provider}")
        
        if provider == 'OPENAI':
            while True:
                api_key = input("Enter your OpenAI API key: ")
                if validate_api_key(api_key):
                    api_keys['OPENAI_API_KEY'] = api_key
                    
                    # Recommend default model
                    print(f"\n{Colors.CYAN}Recommended OpenAI models:{Colors.ENDC}")
                    for i, model in enumerate(model_aliases['OPENAI'], 1):
                        print(f"{Colors.CYAN}[{i}] {Colors.GREEN}{model}{Colors.ENDC}")
                    
                    model_choice = input("Select default model (1-4) or press Enter for gpt-4o: ").strip()
                    if not model_choice:
                        model_info['default_model'] = 'openai/gpt-4o'
                    elif model_choice.isdigit() and 1 <= int(model_choice) <= len(model_aliases['OPENAI']):
                        model_info['default_model'] = model_aliases['OPENAI'][int(model_choice) - 1]
                    else:
                        model_info['default_model'] = 'openai/gpt-4o'
                        print_warning(f"Invalid selection, using default: openai/gpt-4o")
                    break
                print_error("Invalid API key format. It should be at least 10 characters long.")
        
        elif provider == 'ANTHROPIC':
            while True:
                api_key = input("Enter your Anthropic API key: ")
                if validate_api_key(api_key):
                    api_keys['ANTHROPIC_API_KEY'] = api_key
                    
                    # Recommend default model
                    print(f"\n{Colors.CYAN}Recommended Anthropic models:{Colors.ENDC}")
                    for i, model in enumerate(model_aliases['ANTHROPIC'], 1):
                        print(f"{Colors.CYAN}[{i}] {Colors.GREEN}{model}{Colors.ENDC}")
                    
                    model_choice = input("Select default model (1-3) or press Enter for claude-3-7-sonnet: ").strip()
                    if not model_choice or model_choice == '1':
                        model_info['default_model'] = 'anthropic/claude-3-7-sonnet-latest'
                    elif model_choice.isdigit() and 1 <= int(model_choice) <= len(model_aliases['ANTHROPIC']):
                        model_info['default_model'] = model_aliases['ANTHROPIC'][int(model_choice) - 1]
                    else:
                        model_info['default_model'] = 'anthropic/claude-3-7-sonnet-latest'
                        print_warning(f"Invalid selection, using default: anthropic/claude-3-7-sonnet-latest")
                    break
                print_error("Invalid API key format. It should be at least 10 characters long.")
        
        elif provider == 'OPENROUTER':
            while True:
                api_key = input("Enter your OpenRouter API key: ")
                if validate_api_key(api_key):
                    api_keys['OPENROUTER_API_KEY'] = api_key
                    api_keys['OPENROUTER_API_BASE'] = 'https://openrouter.ai/api/v1'

                    # Recommend default model
                    print(f"\n{Colors.CYAN}Recommended OpenRouter models:{Colors.ENDC}")
                    for i, model in enumerate(model_aliases['OPENROUTER'], 1):
                        print(f"{Colors.CYAN}[{i}] {Colors.GREEN}{model}{Colors.ENDC}")
                    
                    model_choice = input("Select default model (1-3) or press Enter for gemini-2.5-flash: ").strip()
                    if not model_choice or model_choice == '1':
                        model_info['default_model'] = 'openrouter/google/gemini-2.5-flash-preview'
                    elif model_choice.isdigit() and 1 <= int(model_choice) <= len(model_aliases['OPENROUTER']):
                        model_info['default_model'] = model_aliases['OPENROUTER'][int(model_choice) - 1]
                    else:
                        model_info['default_model'] = 'openrouter/google/gemini-2.5-flash-preview'
                        print_warning(f"Invalid selection, using default: openrouter/google/gemini-2.5-flash-preview")
                    break
                print_error("Invalid API key format. It should be at least 10 characters long.")

        elif provider == 'OLLAMA':
            print_info("Configuring Ollama (local models)")
            while True:
                ollama_base_url = input(f"Enter your Ollama API Base URL (default: http://localhost:11434): ").strip()
                if not ollama_base_url:
                    ollama_base_url = "http://localhost:11434"
                
                if validate_url(ollama_base_url):
                    api_keys['OLLAMA_API_BASE'] = ollama_base_url
                    break
                print_error("Invalid URL format. Please enter a valid URL (e.g., http://localhost:11434).")

            ollama_api_key = input("Enter your Ollama API Key (optional, usually not needed for local instances, press Enter to skip): ").strip()
            if ollama_api_key:
                api_keys['OLLAMA_API_KEY'] = ollama_api_key
            
            print_info("Ensure your Ollama server is running and you have pulled the desired models (e.g., 'ollama pull llama3').")
            
            # Default model for Ollama if it's the only one selected or if user is prompted
            if len(selected_providers) == 1 and provider == 'OLLAMA': # Ollama is the only provider
                 print(f"\n{Colors.CYAN}Available example Ollama models (ensure you have them pulled):{Colors.ENDC}")
                 for i, model in enumerate(model_aliases['OLLAMA'], 1):
                     print(f"{Colors.CYAN}[{i}] {Colors.GREEN}{model}{Colors.ENDC}")
                 ollama_model_choice = input(f"Select default Ollama model to use (e.g., ollama/llama3) or press Enter to skip: ").strip()
                 if ollama_model_choice:
                     # Check if input is a number corresponding to an alias
                     if ollama_model_choice.isdigit() and 1 <= int(ollama_model_choice) <= len(model_aliases['OLLAMA']):
                         model_info['default_model'] = model_aliases['OLLAMA'][int(ollama_model_choice) - 1]
                     else: # Assume user entered a full model name
                         model_info['default_model'] = ollama_model_choice
                 else:
                     print_warning("No default model specified for Ollama. You will need to configure MODEL_TO_USE in the .env file later.")


    # Default model logic adjustments
    if 'default_model' not in model_info: # If no model was set during provider-specific prompts
        if 'OLLAMA_API_BASE' in api_keys and len(selected_providers) > 1: # Ollama is selected along with others, but no default yet
            # If ollama is among selected, but no default model picked yet (e.g. user skipped for other providers)
            # We prioritize other known providers first if they were selected.
            # This logic might need refinement based on desired priority if multiple are selected.
            if 'ANTHROPIC_API_KEY' in api_keys:
                 model_info['default_model'] = 'anthropic/claude-3-7-sonnet-latest'
            elif 'OPENAI_API_KEY' in api_keys:
                 model_info['default_model'] = 'openai/gpt-4o'
            elif 'OPENROUTER_API_KEY' in api_keys:
                 model_info['default_model'] = 'openrouter/google/gemini-2.5-flash-preview'
            else: # Only Ollama was selected, but user didn't pick a model in the OLLAMA block
                print(f"\n{Colors.CYAN}Available example Ollama models (ensure you have them pulled):{Colors.ENDC}")
                for i, model in enumerate(model_aliases['OLLAMA'], 1):
                    print(f"{Colors.CYAN}[{i}] {Colors.GREEN}{model}{Colors.ENDC}")
                ollama_model_choice = input(f"Select default Ollama model to use (e.g., ollama/llama3) or press Enter to skip: ").strip()
                if ollama_model_choice:
                    if ollama_model_choice.isdigit() and 1 <= int(ollama_model_choice) <= len(model_aliases['OLLAMA']):
                        model_info['default_model'] = model_aliases['OLLAMA'][int(ollama_model_choice) - 1]
                    else:
                        model_info['default_model'] = ollama_model_choice
                # If still no model, it will be handled by the final check below.

        elif 'ANTHROPIC_API_KEY' in api_keys:
            model_info['default_model'] = 'anthropic/claude-3-7-sonnet-latest'
        elif 'OPENAI_API_KEY' in api_keys:
            model_info['default_model'] = 'openai/gpt-4o'
        elif 'OPENROUTER_API_KEY' in api_keys:
            model_info['default_model'] = 'openrouter/google/gemini-2.5-flash-preview'
        # If only Ollama was selected and default model was not set in its block, this will be caught below.

    if 'default_model' in model_info:
        print_success(f"Using {model_info['default_model']} as the default model")
    else:
        # This case should ideally only be hit if Ollama was the only provider and the user skipped selecting a model.
        print_warning("No default model has been set. Please ensure MODEL_TO_USE is set in your .env file.")
        # To prevent 'MODEL_TO_USE' from being empty and causing issues later, we can set it to an empty string here
        # and rely on the user to fill it in the .env file.
        model_info['default_model'] = '' # Explicitly set to empty if none chosen
    
    # Add the default model to the API keys dictionary
    api_keys['MODEL_TO_USE'] = model_info['default_model']
    
    return api_keys

def collect_search_api_keys(existing_info={}):
    """Collect search API keys, allowing use of existing info if available."""
    if existing_info and \
       existing_info.get('TAVILY_API_KEY') and \
       existing_info.get('FIRECRAWL_API_KEY'):
        # FIRECRAWL_URL is also important, even if it's the default
        
        print_info("Found existing Search/Scrape API Key configuration:")
        print_info(f"  Tavily API Key: {mask_api_key(existing_info['TAVILY_API_KEY'])}")
        print_info(f"  Firecrawl API Key: {mask_api_key(existing_info['FIRECRAWL_API_KEY'])}")
        firecrawl_url = existing_info.get('FIRECRAWL_URL', "https://api.firecrawl.dev") # Default if not set
        print_info(f"  Firecrawl URL: {mask_url(firecrawl_url) if firecrawl_url != 'https://api.firecrawl.dev' else firecrawl_url}")

        use_existing = input(f"{Colors.YELLOW}Do you want to use this saved configuration? (yes/no, default: yes): {Colors.ENDC}").strip().lower()
        if use_existing in ['', 'yes', 'y']:
            print_info("Using saved Search/Scrape API Key configuration.")
            return existing_info
        else:
            print_info("Proceeding to collect new Search/Scrape API Key information.")

    print_info("You'll need to obtain API keys for search and web scraping")
    print_info("Visit https://tavily.com/ to get a Tavily API key")
    print_info("Visit https://firecrawl.dev/ to get a Firecrawl API key")
    
    while True:
        tavily_api_key = input("Enter your Tavily API key: ")
        if validate_api_key(tavily_api_key):
            break
        print_error("Invalid API key format. It should be at least 10 characters long.")
    
    while True:
        firecrawl_api_key = input("Enter your Firecrawl API key: ")
        if validate_api_key(firecrawl_api_key):
            break
        print_error("Invalid API key format. It should be at least 10 characters long.")
    
    # Ask if user is self-hosting Firecrawl
    is_self_hosted = input("Are you self-hosting Firecrawl? (y/n): ").lower().strip() == 'y'
    firecrawl_url = "https://api.firecrawl.dev"  # Default URL
    
    if is_self_hosted:
        while True:
            custom_url = input("Enter your Firecrawl URL (e.g., https://your-firecrawl-instance.com): ")
            if validate_url(custom_url):
                firecrawl_url = custom_url
                break
            print_error("Invalid URL format. Please enter a valid URL.")
    
    return {
        'TAVILY_API_KEY': tavily_api_key,
        'FIRECRAWL_API_KEY': firecrawl_api_key,
        'FIRECRAWL_URL': firecrawl_url,
    }

def collect_rapidapi_keys(existing_info={}):
    """Collect RapidAPI key (optional), allowing use of existing info if available."""
    if existing_info and 'RAPID_API_KEY' in existing_info: # Key exists, even if empty
        print_info("Found existing RapidAPI Key configuration:")
        rapid_api_key_value = existing_info['RAPID_API_KEY']
        if rapid_api_key_value:
            print_info(f"  RapidAPI Key: {mask_api_key(rapid_api_key_value)}")
        else:
            print_info("  RapidAPI Key: Not set (optional)")
            
        use_existing = input(f"{Colors.YELLOW}Do you want to use this saved configuration? (yes/no, default: yes): {Colors.ENDC}").strip().lower()
        if use_existing in ['', 'yes', 'y']:
            print_info("Using saved RapidAPI Key configuration.")
            return existing_info
        else:
            print_info("Proceeding to collect new RapidAPI Key information (or skip).")

    print_info("To enable API services like LinkedIn, and others, you'll need a RapidAPI key")
    print_info("Each service requires individual activation in your RapidAPI account:")
    print_info("1. Locate the service's `base_url` in its corresponding file (e.g., https://linkedin-data-scraper.p.rapidapi.com in backend/agent/tools/data_providers/LinkedinProvider.py)")
    print_info("2. Visit that specific API on the RapidAPI marketplace")
    print_info("3. Subscribe to th`e service (many offer free tiers with limited requests)")
    print_info("4. Once subscribed, the service will be available to your agent through the API Services tool")
    print_info("A RapidAPI key is optional for API services like LinkedIn")
    print_info("Visit https://rapidapi.com/ to get your API key if needed")
    print_info("You can leave this blank and add it later if desired")
    
    rapid_api_key = input("Enter your RapidAPI key (optional, press Enter to skip): ")
    
    # Allow empty key
    if not rapid_api_key:
        print_info("Skipping RapidAPI key setup. You can add it later if needed.")
    else:
        # Validate if not empty
        if not validate_api_key(rapid_api_key, allow_empty=True):
            print_warning("The API key format seems invalid, but continuing anyway.")
    
    return {
        'RAPID_API_KEY': rapid_api_key,
    }

def configure_backend_env(env_vars, use_docker=True):
    """Configure backend .env file"""
    env_path = os.path.join('backend', '.env')
    
    # Redis configuration (based on deployment method)
    redis_host = 'redis' if use_docker else 'localhost'
    redis_config = {
        'REDIS_HOST': redis_host,
        'REDIS_PORT': '6379',
        'REDIS_PASSWORD': '',
        'REDIS_SSL': 'false',
    }

    # RabbitMQ configuration (based on deployment method)
    rabbitmq_host = 'rabbitmq' if use_docker else 'localhost'
    rabbitmq_config = {
        'RABBITMQ_HOST': rabbitmq_host,
        'RABBITMQ_PORT': '5672',
    }
    
    # Organize all configuration
    all_config = {}
    
    # Create a string with the formatted content
    env_content = """# Generated by Suna setup script

# Environment Mode
# Valid values: local, staging, production
ENV_MODE=local

#DATABASE
"""

    # Supabase section
    for key, value in env_vars['supabase'].items():
        env_content += f"{key}={value}\n"
    
    # Redis section
    env_content += "\n# REDIS\n"
    for key, value in redis_config.items():
        env_content += f"{key}={value}\n"
    
    # RabbitMQ section
    env_content += "\n# RABBITMQ\n"
    for key, value in rabbitmq_config.items():
        env_content += f"{key}={value}\n"
    
    # LLM section
    env_content += "\n# LLM Providers:\n"
    # Add empty values for all LLM providers we support
    all_llm_keys = ['ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'GROQ_API_KEY', 'OPENROUTER_API_KEY', 'OLLAMA_API_BASE', 'OLLAMA_API_KEY', 'MODEL_TO_USE']
    # Add AWS keys separately
    aws_keys = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_REGION_NAME']
    
    # First add the keys that were provided
    for key, value in env_vars['llm'].items():
        if key in all_llm_keys:
            env_content += f"{key}={value}\n"
            # Remove from the list once added
            if key in all_llm_keys:
                all_llm_keys.remove(key)
    
    # Add empty values for any remaining LLM keys
    for key in all_llm_keys:
        env_content += f"{key}=\n"
    
    # AWS section
    env_content += "\n# AWS Bedrock\n"
    for key in aws_keys:
        value = env_vars['llm'].get(key, '')
        env_content += f"{key}={value}\n"
    
    # Additional OpenRouter params
    if 'OR_SITE_URL' in env_vars['llm'] or 'OR_APP_NAME' in env_vars['llm']:
        env_content += "\n# OpenRouter Additional Settings\n"
        if 'OR_SITE_URL' in env_vars['llm']:
            env_content += f"OR_SITE_URL={env_vars['llm']['OR_SITE_URL']}\n"
        if 'OR_APP_NAME' in env_vars['llm']:
            env_content += f"OR_APP_NAME={env_vars['llm']['OR_APP_NAME']}\n"
    
    # DATA APIs section
    env_content += "\n# DATA APIS\n"
    for key, value in env_vars['rapidapi'].items():
        env_content += f"{key}={value}\n"
    
    # Web search section
    env_content += "\n# WEB SEARCH\n"
    tavily_key = env_vars['search'].get('TAVILY_API_KEY', '')
    env_content += f"TAVILY_API_KEY={tavily_key}\n"
    
    # Web scrape section
    env_content += "\n# WEB SCRAPE\n"
    firecrawl_key = env_vars['search'].get('FIRECRAWL_API_KEY', '')
    firecrawl_url = env_vars['search'].get('FIRECRAWL_URL', '')
    env_content += f"FIRECRAWL_API_KEY={firecrawl_key}\n"
    env_content += f"FIRECRAWL_URL={firecrawl_url}\n"
    
    # Daytona section
    env_content += "\n# Sandbox container provider:\n"
    for key, value in env_vars['daytona'].items():
        env_content += f"{key}={value}\n"
    
    # Add next public URL at the end
    env_content += f"NEXT_PUBLIC_URL=http://localhost:3000\n"
    
    # Write to file
    with open(env_path, 'w') as f:
        f.write(env_content)
    
    print_success(f"Backend .env file created at {env_path}")
    print_info(f"Redis host is set to: {redis_host}")
    print_info(f"RabbitMQ host is set to: {rabbitmq_host}")

def configure_frontend_env(env_vars, use_docker=True):
    """Configure frontend .env.local file"""
    env_path = os.path.join('frontend', '.env.local')
    
    # Use the appropriate backend URL based on start method
    backend_url = "http://localhost:8000/api"

    config = {
        'NEXT_PUBLIC_SUPABASE_URL': env_vars['supabase']['SUPABASE_URL'],
        'NEXT_PUBLIC_SUPABASE_ANON_KEY': env_vars['supabase']['SUPABASE_ANON_KEY'],
        'NEXT_PUBLIC_BACKEND_URL': backend_url,
        'NEXT_PUBLIC_URL': 'http://localhost:3000',
        'NEXT_PUBLIC_ENV_MODE': 'LOCAL',
    }

    # Write to file
    with open(env_path, 'w') as f:
        for key, value in config.items():
            f.write(f"{key}={value}\n")
    
    print_success(f"Frontend .env.local file created at {env_path}")
    print_info(f"Backend URL is set to: {backend_url}")

def setup_supabase(existing_config={}, setup_completed=False):
    """Setup Supabase database, allowing skipping if previously completed."""
    if setup_completed:
        print_info("Supabase setup was previously completed. Skipping.")
        return

    print_info("Setting up Supabase database...")
    
    supabase_url = existing_config.get('SUPABASE_URL')
    if not supabase_url:
        print_error("Supabase URL not found in the provided configuration. This is required for Supabase setup.")
        print_info("Please ensure Supabase information is collected correctly before this step.")
        sys.exit(1)
    
    # Check if the Supabase CLI is installed
    try:
        subprocess.run(
            ['supabase', '--version'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            shell=IS_WINDOWS
        )
        print_success("Supabase CLI is installed.")
    except (subprocess.SubprocessError, FileNotFoundError):
        print_error("Supabase CLI is not initially detected.")
        manual_install_message = "Please install Supabase CLI manually by following instructions at https://supabase.com/docs/guides/cli/getting-started"
        installation_attempted = False

        if IS_WINDOWS:
            scoop_available_in_session = False
            # Try to detect Scoop first
            try:
                subprocess.run(['scoop', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                print_info("Scoop is available.")
                scoop_available_in_session = True
            except (subprocess.SubprocessError, FileNotFoundError):
                # Scoop is not found, attempt to install it
                print_info("Scoop not found. Attempting to install Scoop...")
                try:
                    # Command to set execution policy and install Scoop
                    scoop_install_command = 'powershell -ExecutionPolicy Bypass -Command "Set-ExecutionPolicy RemoteSigned -scope CurrentUser -Force; irm get.scoop.sh | iex"'
                    print_info(f"Executing Scoop install: {scoop_install_command}")
                    scoop_install_process = subprocess.run(
                        scoop_install_command,
                        shell=True, check=True, capture_output=True, text=True
                    )
                    print_success("Scoop installation script executed successfully.")
                    # Showing stdout/stderr for scoop install can be very verbose, only show on error or if debug needed.
                    # if scoop_install_process.stdout: print_info(f"Scoop install stdout:\n{scoop_install_process.stdout}")
                    # if scoop_install_process.stderr: print_warning(f"Scoop install stderr:\n{scoop_install_process.stderr}")

                    print_info("Attempting to locate Scoop shims directory to update PATH for current session...")
                    user_home = os.path.expanduser("~")
                    scoop_shims_path = os.path.join(user_home, "scoop", "shims")
                    if os.path.isdir(scoop_shims_path):
                        print_info(f"Found Scoop shims at {scoop_shims_path}. Prepending to PATH for this session.")
                        os.environ["PATH"] = scoop_shims_path + os.pathsep + os.environ.get("PATH", "")
                    else:
                        print_warning(f"Scoop shims directory not found at expected location: {scoop_shims_path}. PATH not modified.")
                    
                    print_warning("Scoop installation attempted. This may require opening a new terminal for PATH changes to take full effect.")
                    print_info("Attempting to verify Scoop installation in the current session...")
                    try:
                        # Try to run scoop --version again
                        subprocess.run(['scoop', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                        print_success("Scoop successfully verified in current session after installation attempt.")
                        scoop_available_in_session = True
                    except (subprocess.SubprocessError, FileNotFoundError) as e_scoop_verify_after_install:
                        print_error(f"Scoop was installed, but 'scoop --version' still fails in this session: {e_scoop_verify_after_install}")
                        manual_install_message += " (Scoop installed, but PATH update likely needed. Please re-run setup in a new terminal)."
                        print_info(manual_install_message)
                        sys.exit(1) # Critical: if scoop was "installed" but isn't usable, user must intervene.
                except subprocess.SubprocessError as e_scoop_install:
                    print_error(f"Automated Scoop installation via PowerShell failed: {e_scoop_install}")
                    if hasattr(e_scoop_install, 'stdout') and e_scoop_install.stdout and e_scoop_install.stdout.strip():
                        print_error(f"Scoop PowerShell install stdout:\n{e_scoop_install.stdout.strip()}")
                    if hasattr(e_scoop_install, 'stderr') and e_scoop_install.stderr and e_scoop_install.stderr.strip():
                        print_error(f"Scoop PowerShell install stderr:\n{e_scoop_install.stderr.strip()}")
                    print_info("This might be due to PowerShell execution policies or other system restrictions.")
                    manual_install_message += " (Automated Scoop installation via PowerShell failed)."
                    # Fall through, scoop_available_in_session remains False
                except FileNotFoundError: # powershell.exe not found
                    print_error("PowerShell not found. Cannot attempt Scoop installation.")
                    manual_install_message += " (PowerShell not found, so Scoop could not be installed automatically)."
                    # Fall through, scoop_available_in_session remains False

            if scoop_available_in_session:
                print_info("Attempting to install Supabase CLI using Scoop...")
                try:
                    subprocess.run(['scoop', 'install', 'supabase'], check=True, shell=True, capture_output=True, text=True)
                    print_success("Supabase CLI installation via Scoop initiated.")
                    installation_attempted = True
                except subprocess.SubprocessError as e_supabase_scoop:
                    print_warning(f"Initial 'scoop install supabase' failed: {e_supabase_scoop}")
                    if hasattr(e_supabase_scoop, 'stdout') and e_supabase_scoop.stdout and e_supabase_scoop.stdout.strip():
                        print_warning(f"Scoop install (1st attempt) stdout:\n{e_supabase_scoop.stdout.strip()}")
                    if hasattr(e_supabase_scoop, 'stderr') and e_supabase_scoop.stderr and e_supabase_scoop.stderr.strip():
                        print_warning(f"Scoop install (1st attempt) stderr:\n{e_supabase_scoop.stderr.strip()}")
                    print_info("This can happen if the Supabase bucket isn't added to Scoop. Attempting to add bucket and retry...")
                    try:
                        subprocess.run(['scoop', 'bucket', 'add', 'supabase', 'https://github.com/supabase/scoop-bucket.git'], check=True, shell=True, capture_output=True, text=True)
                        print_success("Supabase Scoop bucket added successfully.")
                        print_info("Retrying 'scoop install supabase'...")
                        subprocess.run(['scoop', 'install', 'supabase'], check=True, shell=True, capture_output=True, text=True)
                        print_success("Supabase CLI installation via Scoop (after adding bucket) initiated successfully.")
                        installation_attempted = True
                    except subprocess.SubprocessError as e_scoop_retry:
                        print_error(f"Failed to install Supabase CLI via Scoop even after adding bucket: {e_scoop_retry}")
                        if hasattr(e_scoop_retry, 'stdout') and e_scoop_retry.stdout and e_scoop_retry.stdout.strip():
                            print_error(f"Scoop install (2nd attempt) stdout:\n{e_scoop_retry.stdout.strip()}")
                        if hasattr(e_scoop_retry, 'stderr') and e_scoop_retry.stderr and e_scoop_retry.stderr.strip():
                            print_error(f"Scoop install (2nd attempt) stderr:\n{e_scoop_retry.stderr.strip()}")
                        manual_install_message += " (Scoop available, but Supabase CLI install via Scoop failed even after bucket add)."
            # If scoop_available_in_session is False (either initially or because auto-install failed/wasn't usable):
            # The manual_install_message should have been updated in the clauses above.
            # The script will then proceed to the 'if not installation_attempted:' block later.

        elif platform.system() == 'Darwin' or platform.system() == 'Linux':
            system_name = "macOS" if platform.system() == 'Darwin' else "Linux"
            print_info(f"Attempting to install Supabase CLI using Homebrew for {system_name}...")
            try:
                subprocess.run(['brew', '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True)
                print_info("Homebrew is available. Attempting to install Supabase CLI...")
                subprocess.run(['brew', 'install', 'supabase/tap/supabase'], check=True, shell=True)
                print_success("Supabase CLI installation via Homebrew initiated.")
                installation_attempted = True
            except (subprocess.SubprocessError, FileNotFoundError) as e_brew:
                print_error(f"Failed to install Supabase CLI via Homebrew: {e_brew}")
                manual_install_message += f" (Homebrew install failed on {system_name})."
        
        else: # Other operating systems
            print_warning(f"Unsupported OS for automatic Supabase CLI installation: {platform.system()}")

        if installation_attempted:
            print_info("Verifying Supabase CLI installation...")
            try:
                subprocess.run(
                    ['supabase', '--version'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    shell=IS_WINDOWS # Use IS_WINDOWS for shell=True consistency
                )
                print_success("Supabase CLI successfully installed and verified.")
            except (subprocess.SubprocessError, FileNotFoundError):
                print_error("Supabase CLI was reportedly installed (e.g., via Scoop or Homebrew), but 'supabase --version' still fails in this session.")
                print_info("This could be due to PATH environment variable issues not fully resolved in the current session, or an incomplete installation.")
                if IS_WINDOWS and scoop_available_in_session : # More specific guidance if Scoop was involved
                    print_info("Since Scoop was used on Windows, please try the following in a NEW PowerShell terminal:")
                    print_info("  1. Run 'scoop --version'.")
                    print_info("     - If this fails, Scoop is not correctly installed or its shims directory (usually C:\\Users\\YourUser\\scoop\\shims) is not in your PATH. Please verify your Scoop installation and PATH.")
                    print_info("  2. If 'scoop --version' works, run 'scoop install supabase' (it might say it's already installed, which is fine).")
                    print_info("  3. Then, run 'supabase --version'.")
                    print_info("  4. If all these manual steps succeed in the new terminal, please re-run this setup script (`python setup.py`).")
                else: # General guidance for other OS or if Scoop wasn't the method
                    print_info("Please open a new terminal/command prompt and try running 'supabase --version' there.")
                    print_info("If it works in the new terminal, re-run this setup script from that new terminal.")
                    print_info("If it still fails, you may need to troubleshoot your Supabase CLI installation or PATH configuration manually.")
                print_info(f"Original installation guidance if needed: {manual_install_message}")
                sys.exit(1) # Exit if Supabase CLI verification fails after an attempt
        
        # This block is reached if:
        # 1. IS_WINDOWS was true, but scoop_available_in_session ended up false AND installation_attempted (for Supabase CLI) remained false.
        # 2. Or, if it wasn't Windows/macOS/Linux.
        # 3. Or, if an installation method for other OS was attempted but failed, and installation_attempted is still false.
        if not installation_attempted: # If no installation path was successfully completed for Supabase CLI
            print_error("Supabase CLI is not installed and no automated installation method succeeded or was applicable for your OS.")
            print_info(manual_install_message) # This message should now contain context about Scoop/Brew attempts if any.
            print_info("After installing Supabase CLI manually, please re-run this setup script.")
            sys.exit(1)

    # Extract project reference from Supabase URL (already fetched from existing_config)
    project_ref = None
    if supabase_url: # supabase_url is now from existing_config
        match = re.search(r'https://([^.]+)\.supabase\.co', supabase_url) # supabase_url is from existing_config
        if match:
            project_ref = match.group(1)
            print_success(f"Extracted project reference '{project_ref}' from Supabase URL: {mask_url(supabase_url)}")
    
    # If extraction failed, ask the user (should be rare if config is good)
    if not project_ref:
        print_warning("Could not automatically extract project reference from the provided Supabase URL.")
        print_info("You can find your project reference in your Supabase project's settings, usually part of the URL (e.g., 'your-project-ref' in 'https://your-project-ref.supabase.co').")
        project_ref = input("Please enter your Supabase project reference: ").strip()
        if not project_ref:
            print_error("Supabase project reference is required. Exiting.")
            sys.exit(1)
    
    # Change the working directory to backend
    backend_dir = os.path.join(os.getcwd(), 'backend')
    print_info(f"Changing to backend directory: {backend_dir}")
    
    try:
        # Login to Supabase CLI (interactive)
        print_info("Logging into Supabase CLI...")
        subprocess.run(['supabase', 'login'], check=True, shell=IS_WINDOWS)
        
        # Link to project
        print_info(f"Linking to Supabase project {project_ref}...")
        subprocess.run(
            ['supabase', 'link', '--project-ref', project_ref],
            cwd=backend_dir,
            check=True,
            shell=IS_WINDOWS
        )
        
        # Push database migrations
        print_info("Pushing database migrations...")
        subprocess.run(
            ['supabase', 'db', 'push'],
            cwd=backend_dir,
            check=True,
            shell=IS_WINDOWS
        )
        
        print_success("Supabase database setup completed")
        
        # Reminder for manual step
        print_warning("IMPORTANT: You need to manually expose the 'basejump' schema in Supabase")
        print_info("Go to the Supabase web platform -> choose your project -> Project Settings -> Data API")
        print_info("In the 'Exposed Schema' section, add 'basejump' if not already there")
        input("Press Enter once you've completed this step...")
        
    except subprocess.SubprocessError as e:
        print_error(f"Failed to setup Supabase: {e}")
        sys.exit(1)

def install_dependencies(dependencies_installed=False):
    """Install frontend and backend dependencies, allowing skipping if previously completed."""
    if dependencies_installed:
        print_info("Dependencies were previously installed. Skipping.")
        return True # Indicate success as they are already installed

    print_info("Installing required dependencies...")
    # START DEBUG BLOCK
    print(f"{Colors.YELLOW}DEBUG: Current sys.executable (in setup.py): {sys.executable}{Colors.ENDC}")
    print(f"{Colors.YELLOW}DEBUG: Current os.getcwd(): {os.getcwd()}{Colors.ENDC}")
    print(f"{Colors.YELLOW}DEBUG: Current PATH (in setup.py): {os.environ.get('PATH')}{Colors.ENDC}")
    print(f"{Colors.YELLOW}DEBUG: VENV_PATH variable: {VENV_PATH}{Colors.ENDC}")
    print(f"{Colors.YELLOW}DEBUG: VENV_ACTIVATION_MARKER ('_SUNA_SETUP_IN_VENV_') is set: {os.environ.get(VENV_ACTIVATION_MARKER) == '1'}{Colors.ENDC}")

    print(f"{Colors.YELLOW}DEBUG: Running '{sys.executable} -m pip --version' from setup.py...{Colors.ENDC}")
    try:
        pip_version_process = subprocess.run(
            [sys.executable, '-m', 'pip', '--version'],
            capture_output=True, text=True, check=False, shell=False
        )
        print(f"{Colors.YELLOW}DEBUG: pip --version stdout:\n{pip_version_process.stdout}{Colors.ENDC}") # Escaped newline for clarity in agent log
        if pip_version_process.stderr:
            print(f"{Colors.RED}DEBUG: pip --version stderr:\n{pip_version_process.stderr}{Colors.ENDC}") # Escaped newline
    except Exception as e_pip_version:
        print(f"{Colors.RED}DEBUG: Error running pip --version: {e_pip_version}{Colors.ENDC}")

    # Using triple quotes for the script to avoid escaping issues with quotes inside
    py_path_details_script = """
import sys, os, shutil
print(f'DEBUG_SUBPROCESS: sys.executable: {sys.executable}')
print(f'DEBUG_SUBPROCESS: sys.prefix: {sys.prefix}')
print(f'DEBUG_SUBPROCESS: sys.base_prefix: {sys.base_prefix}')
print(f'DEBUG_SUBPROCESS: os.getcwd(): {os.getcwd()}')
print(f'DEBUG_SUBPROCESS: sys.path: {sys.path}')
print(f'DEBUG_SUBPROCESS: shutil.which("poetry"): {shutil.which("poetry")}')
print(f'DEBUG_SUBPROCESS: shutil.which("pip"): {shutil.which("pip")}')
"""
    print(f"{Colors.YELLOW}DEBUG: Running '{sys.executable} -c \"<see script below>\"' from setup.py...{Colors.ENDC}")
    # For logging, it's hard to show the exact multiline script, so indicate it's complex.
    try:
        py_details_process = subprocess.run(
            [sys.executable, '-c', py_path_details_script],
            capture_output=True, text=True, check=False, shell=False
        )
        print(f"{Colors.YELLOW}DEBUG: Python path details stdout:\n{py_details_process.stdout}{Colors.ENDC}") # Escaped newline
        if py_details_process.stderr:
            print(f"{Colors.RED}DEBUG: Python path details stderr:\n{py_details_process.stderr}{Colors.ENDC}") # Escaped newline
    except Exception as e_py_details:
        print(f"{Colors.RED}DEBUG: Error running Python path details: {e_py_details}{Colors.ENDC}")
    # END DEBUG BLOCK
    
    try:
        # Install frontend dependencies
        print_info("Installing frontend dependencies...")
        subprocess.run(
            ['npm', 'install'], 
            cwd='frontend',
            check=True,
            shell=IS_WINDOWS
        )
        print_success("Frontend dependencies installed successfully")
        
        # Lock dependencies
        print_info("Locking backend dependencies using poetry...")

        poetry_exe_in_venv = os.path.join(VENV_PATH, 'Scripts', 'poetry.exe') # For Windows

        # Determine the base command for poetry
        if os.environ.get(VENV_ACTIVATION_MARKER) == "1":
            poetry_base_command = [sys.executable, '-m', 'poetry']
            print_info(f"Using Suna venv activated Python for Poetry: '{sys.executable} -m poetry'")
        # Fallback logic if not in the marked Suna venv (should be less common for primary setup path)
        elif IS_WINDOWS and os.path.isfile(poetry_exe_in_venv):
            poetry_base_command = [poetry_exe_in_venv]
            print_info(f"Using Poetry executable from venv (Windows specific path): {poetry_exe_in_venv}")
        else: 
            poetry_base_command = ['poetry']
            print_warning(f"Falling back to direct 'poetry' command. This might use a global Poetry installation.")
            if IS_WINDOWS and not os.path.isfile(poetry_exe_in_venv):
                print_warning(f"Poetry executable not found at the expected venv path: {poetry_exe_in_venv}")
            print_warning("If issues arise, ensure Poetry is correctly installed and accessible, or that the Suna venv is active.")

        lock_command = poetry_base_command + ['lock']
        print_info(f"Executing: {' '.join(lock_command)} in backend directory")
        # Determine shell mode for the primary attempt
        # Use shell=True on Windows only if poetry_base_command is just ['poetry']
        # Otherwise (direct path or python -m poetry), use shell=False.
        lock_shell_mode = IS_WINDOWS if poetry_base_command[0] == 'poetry' else False
        try:
            subprocess.run(
                lock_command,
                cwd='backend',
                check=True,
                shell=lock_shell_mode
            )
            print_success("Poetry lock successful.")
        except subprocess.SubprocessError as e_lock:
            print_error(f"Poetry lock failed with '{' '.join(lock_command)}': {e_lock}")
            # If the initial attempt was with poetry_exe_in_venv (Windows specific path) and it failed,
            # and we hadn't already fallen back to direct 'poetry'
            if IS_WINDOWS and poetry_base_command[0] == poetry_exe_in_venv:
                print_info("Retrying poetry lock with direct 'poetry' command...")
                try:
                    subprocess.run(['poetry', 'lock'], cwd='backend', check=True, shell=IS_WINDOWS)
                    print_success("Poetry lock successful with direct 'poetry' command.")
                except subprocess.SubprocessError as e_lock_direct:
                    print_error(f"Direct 'poetry lock' also failed: {e_lock_direct}")
                    print_info("Please ensure Poetry is installed and accessible. If you installed it via pip in the venv, it might not be in PATH.")
                    print_info("You might need to activate the venv manually or add Poetry's script directory to PATH.")
                    return False # Exit install_dependencies due to failure
            # If the initial attempt was with '[sys.executable, "-m", "poetry"]' (preferred for venv)
            elif poetry_base_command[0] == sys.executable and poetry_base_command[1] == '-m':
                print_info("Retrying poetry lock with direct 'poetry' command as fallback...")
                try:
                    subprocess.run(['poetry', 'lock'], cwd='backend', check=True, shell=IS_WINDOWS)
                    print_success("Poetry lock successful with direct 'poetry' command (fallback).")
                    # Update poetry_base_command for the install step if this fallback succeeded
                    poetry_base_command = ['poetry'] 
                except subprocess.SubprocessError as e_lock_direct:
                    print_error(f"Direct 'poetry lock' (fallback) also failed: {e_lock_direct}")
                    print_info("Please ensure Poetry is installed and accessible, either via python -m poetry in venv or directly in PATH.")
                    return False
            else: # Direct poetry (the initial command) already failed, or some other unhandled case
                 print_info("Poetry lock failed. Please ensure Poetry is installed and accessible.")
                 return False

        # Install backend dependencies
        print_info("Installing backend dependencies using poetry...")
        # poetry_base_command might have been updated to ['poetry'] if the lock step used a fallback.
        install_command = poetry_base_command + ['install']
        print_info(f"Executing: {' '.join(install_command)} in backend directory")
        # Determine shell mode for the primary attempt (can reuse poetry_base_command logic)
        install_shell_mode = IS_WINDOWS if poetry_base_command[0] == 'poetry' else False
        try:
            subprocess.run(
                install_command, 
                cwd='backend',
                check=True,
                shell=install_shell_mode
            )
            print_success("Backend dependencies installed successfully.")
        except subprocess.SubprocessError as e_install:
            print_error(f"Poetry install failed with '{' '.join(install_command)}': {e_install}")
            if IS_WINDOWS and poetry_base_command[0] == poetry_exe_in_venv:
                print_info("Retrying poetry install with direct 'poetry' command...")
                try:
                    subprocess.run(['poetry', 'install'], cwd='backend', check=True, shell=IS_WINDOWS)
                    print_success("Poetry install successful with direct 'poetry' command.")
                except subprocess.SubprocessError as e_install_direct:
                    print_error(f"Direct 'poetry install' also failed: {e_install_direct}")
                    print_info("Please ensure Poetry is installed and accessible.")
                    return False
            # If the initial attempt was with '[sys.executable, "-m", "poetry"]' (preferred for venv)
            elif poetry_base_command[0] == sys.executable and poetry_base_command[1] == '-m':
                 print_info("Retrying poetry install with direct 'poetry' command as fallback...")
                 try:
                    subprocess.run(['poetry', 'install'], cwd='backend', check=True, shell=IS_WINDOWS)
                    print_success("Poetry install successful with direct 'poetry' command (fallback).")
                 except subprocess.SubprocessError as e_install_direct:
                    print_error(f"Direct 'poetry install' (fallback) also failed: {e_install_direct}")
                    print_info("Please ensure Poetry is installed and accessible, either via python -m poetry in venv or directly in PATH.")
                    return False
            else: # Direct poetry (the initial command) already failed
                print_info("Poetry install failed. Please ensure Poetry is installed and accessible.")
                return False
            
        # If we reached here, one of the install attempts succeeded.
        # The print_success("Backend dependencies installed successfully.") was inside the try block
        # and might not be reached if a fallback was used. Let's ensure it's printed if successful.
        # However, the original structure had it after the except block, implying it's a general success message.
        # For clarity, let's assume if no 'return False' was hit, it's a success.
        # The original print_success is fine.
        
        return True
    except subprocess.SubprocessError as e: # This top-level except is primarily for npm install or unhandled poetry errors
        print_error(f"Failed to install dependencies: {e}") 
        if 'npm' in str(e).lower():
            print_error("The error seems related to 'npm install' for frontend dependencies.")
        else:
            print_error("The error seems related to backend 'poetry' commands or another setup step within install_dependencies.")
        print_info("You may need to install them manually or check specific error messages above.")
        return False

def start_suna():
    """Start Suna using Docker Compose or manual startup"""
    print_info("You can start Suna using either Docker Compose or by manually starting the frontend, backend and worker.")

    print(f"\n{Colors.CYAN}How would you like to start Suna?{Colors.ENDC}")
    print(f"{Colors.CYAN}[1] {Colors.GREEN}Docker Compose{Colors.ENDC} {Colors.CYAN}(recommended, starts all services){Colors.ENDC}")
    print(f"{Colors.CYAN}[2] {Colors.GREEN}Manual startup{Colors.ENDC} {Colors.CYAN}(requires Redis, RabbitMQ & separate terminals){Colors.ENDC}\n")
    
    while True:
        start_method = input("Enter your choice (1 or 2): ")
        if start_method in ["1", "2"]:
            break
        print_error("Invalid selection. Please enter '1' for Docker Compose or '2' for Manual startup.")
    
    use_docker = start_method == "1"
    
    if use_docker:
        print_info("Starting Suna with Docker Compose...")
        
        try:
            # TODO: uncomment when we have pre-built images on Docker Hub or GHCR
            # GitHub repository environment variable setup
            # github_repo = None
            
            # print(f"\n{Colors.CYAN}Do you want to use pre-built images or build locally?{Colors.ENDC}")
            # print(f"{Colors.CYAN}[1] {Colors.GREEN}Pre-built images{Colors.ENDC} {Colors.CYAN}(faster){Colors.ENDC}")
            # print(f"{Colors.CYAN}[2] {Colors.GREEN}Build locally{Colors.ENDC} {Colors.CYAN}(customizable){Colors.ENDC}\n")
            
            # while True:
            #     build_choice = input("Enter your choice (1 or 2): ")
            #     if build_choice in ["1", "2"]:
            #         break
            #     print_error("Invalid selection. Please enter '1' for pre-built images or '2' for building locally.")
                
            # use_prebuilt = build_choice == "1"
            
            # if use_prebuilt:
            #     # Get GitHub repository name from user
            #     print_info("For pre-built images, you need to specify a GitHub repository name")
            #     print_info("Example format: your-github-username/repo-name")
                
            #     github_repo = input("Enter GitHub repository name: ")
            #     if not github_repo or "/" not in github_repo:
            #         print_warning("Invalid GitHub repository format. Using a default value.")
            #         # Create a random GitHub repository name as fallback
            #         random_name = ''.join(random.choices(string.ascii_lowercase, k=8))
            #         github_repo = f"user/{random_name}"
                
            #     # Set the environment variable
            #     os.environ["GITHUB_REPOSITORY"] = github_repo
            #     print_info(f"Using GitHub repository: {github_repo}")
                
            #     # Start with pre-built images
            #     print_info("Using pre-built images...")
            #     subprocess.run(['docker', 'compose', '-f', 'docker-compose.ghcr.yaml', 'up', '-d'], check=True)
            # else:
            #     # Start with docker-compose (build images locally)
            #     print_info("Building images locally...")
            #     subprocess.run(['docker', 'compose', 'up', '-d'], check=True)

            print_info("Building images locally...")
            subprocess.run(['docker', 'compose', 'up', '-d', '--build'], check=True, shell=IS_WINDOWS)

            # Wait for services to be ready
            print_info("Waiting for services to start...")
            time.sleep(10)  # Give services some time to start
            
            # Check if services are running
            result = subprocess.run(
                ['docker', 'compose', 'ps', '-q'],
                capture_output=True,
                text=True,
                shell=IS_WINDOWS
            )
            
            if "backend" in result.stdout and "frontend" in result.stdout:
                print_success("Suna services are up and running!")
            else:
                print_warning("Some services might not be running correctly. Check 'docker compose ps' for details.")
            
        except subprocess.SubprocessError as e:
            print_error(f"Failed to start Suna: {e}")
            sys.exit(1)
            
        return use_docker
    else:
        print_info("For manual startup, you'll need to:")
        print_info("1. Start Redis and RabbitMQ in Docker (required for the backend)")
        print_info("2. Start the frontend with npm run dev")
        print_info("3. Start the backend with poetry run python3.11 api.py")
        print_info("4. Start the worker with poetry run python3.11 -m dramatiq run_agent_background")
        print_warning("Note: Redis and RabbitMQ must be running before starting the backend")
        print_info("Detailed instructions will be provided at the end of setup")
        
        return use_docker

def final_instructions(use_docker=True, env_vars=None):
    """Show final instructions"""
    print(f"\n{Colors.GREEN}{Colors.BOLD}✨ Suna Setup Complete! ✨{Colors.ENDC}\n")
    
    # Display LLM configuration info if available
    if env_vars and 'llm' in env_vars and 'MODEL_TO_USE' in env_vars['llm']:
        default_model = env_vars['llm']['MODEL_TO_USE']
        print_info(f"Suna is configured to use {Colors.GREEN}{default_model}{Colors.ENDC} as the default LLM model")

    if use_docker:
        print_info("Your Suna instance is now running!")
        print_info("Access it at: http://localhost:3000")
        print_info("Create an account using Supabase authentication to start using Suna")
        print("\nUseful Docker commands:")
        print(f"{Colors.CYAN}  docker compose ps{Colors.ENDC}         - Check the status of Suna services")
        print(f"{Colors.CYAN}  docker compose logs{Colors.ENDC}       - View logs from all services")
        print(f"{Colors.CYAN}  docker compose logs -f{Colors.ENDC}    - Follow logs from all services")
        print(f"{Colors.CYAN}  docker compose down{Colors.ENDC}       - Stop Suna services")
        print(f"{Colors.CYAN}  docker compose up -d{Colors.ENDC}      - Start Suna services (after they've been stopped)")
    else:
        print_info("Suna setup is complete but services are not running yet.")
        print_info("To start Suna, you need to:")
        
        print_info("1. Start Redis and RabbitMQ (required for backend):")
        print(f"{Colors.CYAN}    cd backend")
        print(f"    docker compose up redis rabbitmq -d{Colors.ENDC}")
        
        print_info("2. In one terminal:")
        print(f"{Colors.CYAN}    cd frontend")
        print(f"    npm run dev{Colors.ENDC}")
        
        print_info("3. In another terminal:")
        print(f"{Colors.CYAN}    cd backend")
        print(f"    poetry run python3.11 api.py{Colors.ENDC}")
        
        print_info("3. In one more terminal:")
        print(f"{Colors.CYAN}    cd backend")
        print(f"    poetry run python3.11 -m dramatiq run_agent_background{Colors.ENDC}")
        
        print_info("4. Once all services are running, access Suna at: http://localhost:3000")
        print_info("5. Create an account using Supabase authentication to start using Suna")

# Path to the virtual environment
VENV_PATH = os.path.join(os.getcwd(), '.venv')
PYTHON_IN_VENV = os.path.join(VENV_PATH, 'Scripts', 'python.exe') if IS_WINDOWS else os.path.join(VENV_PATH, 'bin', 'python')
VENV_ACTIVATION_MARKER = "_SUNA_SETUP_IN_VENV_" # Environment variable to prevent re-launch loop

def get_python_version(python_exe='python'):
    print(f"{Colors.YELLOW}DEBUG_GET_PY_VER: Attempting with: {python_exe}{Colors.ENDC}")
    try:
        cmd_to_run = []
        if ' ' in python_exe and IS_WINDOWS:
            # For commands like "py -3.11" on Windows with shell=True,
            # pass as a single string.
            cmd_to_run = f"{python_exe} --version"
            print(f"{Colors.YELLOW}DEBUG_GET_PY_VER: Running as string (shell=True): {cmd_to_run}{Colors.ENDC}")
        else:
            # For commands without spaces in python_exe, or non-Windows,
            # or if intending shell=False (though current is shell=IS_WINDOWS)
            # construct list. If python_exe itself has parts, split it.
            cmd_parts = python_exe.split()
            cmd_to_run = cmd_parts + ['--version']
            print(f"{Colors.YELLOW}DEBUG_GET_PY_VER: Running as list: {cmd_to_run}{Colors.ENDC}")

        process = subprocess.run(
            cmd_to_run,
            capture_output=True,
            text=True,
            shell=IS_WINDOWS # This remains shell=IS_WINDOWS
        )
        # It's important to check process.returncode if not using check=True,
        # but for version parsing, sometimes programs output version to stderr and exit with error.
        # We will proceed to parse output regardless of return code for now,
        # relying on regex to find version.
        
        raw_stdout = process.stdout.strip() if process.stdout else ''
        raw_stderr = process.stderr.strip() if process.stderr else ''
        
        print(f"{Colors.YELLOW}DEBUG_GET_PY_VER: Return code: {process.returncode}{Colors.ENDC}")
        print(f"{Colors.YELLOW}DEBUG_GET_PY_VER: Raw stdout: {raw_stdout}{Colors.ENDC}")
        print(f"{Colors.YELLOW}DEBUG_GET_PY_VER: Raw stderr: {raw_stderr}{Colors.ENDC}")
        
        version_output = raw_stdout + raw_stderr # Combine both streams
        
        match = re.search(r"Python (\d+\.\d+\.\d+)", version_output)
        parsed_version = None
        if match:
            parsed_version = match.group(1)
            
        print(f"{Colors.YELLOW}DEBUG_GET_PY_VER: Parsed version: {parsed_version}{Colors.ENDC}")
        return parsed_version # Return None if match failed, or the version string
        
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"{Colors.RED}DEBUG_GET_PY_VER: Error during subprocess call for {python_exe}: {e}{Colors.ENDC}")
        return None

def ensure_python_311_and_venv():
    """
    Ensures Python 3.11 is available and the script is running in a venv with it.
    If not, it attempts to install Python 3.11 (on Windows), create the venv,
    and re-launch the setup script from within the venv.
    """
    if os.environ.get(VENV_ACTIVATION_MARKER) == "1":
        print_info("Already running in the Suna setup virtual environment.")
        # Verify it's actually Python 3.11 in the venv
        current_version = get_python_version(sys.executable)
        if current_version and "3.11" in current_version:
            print_success(f"Confirmed Python {current_version} in venv.")
            return # Already in the correct venv
        else:
            print_error(f"Running in a venv, but it's Python {current_version}, not 3.11. Please delete the .venv folder and re-run.")
            sys.exit(1)

    print_info("Checking Python 3.11 and virtual environment...")

    # 1. Check if system Python is 3.11
    system_python_executable = "python" # Default
    python_version = get_python_version(system_python_executable)
    if not (python_version and "3.11" in python_version):
        if IS_WINDOWS:
            # Try 'py -3.11' if 'python' isn't 3.11
            python_version = get_python_version('py -3.11')
            if python_version and "3.11" in python_version:
                system_python_executable = 'py -3.11' # Found a way to call 3.11
                print_success(f"Found Python {python_version} using '{system_python_executable}'.")
            else: # 'python' and 'py -3.11' are not 3.11
                print_warning("System Python is not 3.11. Attempting to install Python 3.11 using winget...")
                success, already_installed = run_winget_install("Python.Python.3.11", "Python 3.11")
                if success:
                    print_info("Winget operation complete. Attempting to locate and use Python 3.11 to proceed automatically...")
                    # Try to determine the Python 3.11 executable path again, specifically trying `py -3.11`.
                    python_version_after_winget = get_python_version('py -3.11')
                    if python_version_after_winget and "3.11" in python_version_after_winget:
                        system_python_executable = 'py -3.11' # Update to use this for venv creation
                        print_success(f"Successfully found Python 3.11 via 'py -3.11' ({python_version_after_winget}). Proceeding with venv creation.")
                        # Allow the function to continue to venv creation without exiting
                    else:
                        if already_installed:
                            print_info("Python 3.11 was already installed (as reported by winget), but 'py -3.11' is not pointing to it or it's not found immediately.")
                        else:
                            print_success("Python 3.11 installation via winget seems to have succeeded.")
                        print_info("However, 'py -3.11' did not immediately resolve to Python 3.11.")
                        print_info("Please re-run this script in a new terminal for changes to take effect and for Python 3.11 to be detected.")
                        sys.exit(0) # Exit for user to re-run in new terminal
                else:
                    print_error("Failed to install Python 3.11 using winget.")
                    print_info("Please install Python 3.11 manually from https://www.python.org/downloads/ and ensure it's in your PATH.")
                    sys.exit(1)
        else: # Not Windows
            print_error("Python 3.11 is required but not found in your PATH.")
            print_info("Please install Python 3.11 manually from https://www.python.org/downloads/ or use your system's package manager.")
            sys.exit(1)
    else: # 'python' command is already 3.11
        print_success(f"Found Python {python_version} using '{system_python_executable}'.")


    # 2. Create or verify the virtual environment
    if not os.path.exists(VENV_PATH):
        print_info(f"Creating virtual environment at: {VENV_PATH} using {system_python_executable}")
        try:
            # Logic to determine cmd_for_venv for venv creation
            cmd_for_venv_str_or_list = []
            if ' ' in system_python_executable and IS_WINDOWS:
                cmd_for_venv_str_or_list = f"{system_python_executable} -m venv {VENV_PATH}"
                print(f"{Colors.YELLOW}DEBUG_VENV_CREATE: Running as string (shell=True): {cmd_for_venv_str_or_list}{Colors.ENDC}")
            else:
                cmd_parts_venv = system_python_executable.split()
                cmd_for_venv_str_or_list = cmd_parts_venv + ['-m', 'venv', VENV_PATH]
                print(f"{Colors.YELLOW}DEBUG_VENV_CREATE: Running as list: {cmd_for_venv_str_or_list}{Colors.ENDC}")
            
            # Use the confirmed Python 3.11 executable to create the venv
            subprocess.run(cmd_for_venv_str_or_list, check=True, shell=IS_WINDOWS)
            print_success("Virtual environment created successfully.")
        except subprocess.SubprocessError as e:
            print_error(f"Failed to create virtual environment: {e}")
            sys.exit(1)
    else: # VENV_PATH exists
        print_info(f"Virtual environment directory '{VENV_PATH}' already exists.")
        # Check if the Python in this venv is 3.11
        venv_python_version = get_python_version(PYTHON_IN_VENV)
        if venv_python_version and "3.11" in venv_python_version:
            print_success(f"Existing venv uses Python {venv_python_version}.")
        else:
            print_warning(f"Existing venv at '{VENV_PATH}' does not seem to use Python 3.11 (found {venv_python_version}).")
            recreate_venv = input(f"{Colors.YELLOW}Do you want to remove the existing .venv and recreate it? (yes/no): {Colors.ENDC}").strip().lower()
            if recreate_venv in ['yes', 'y']:
                try:
                    import shutil
                    shutil.rmtree(VENV_PATH)
                    print_info(f"Removed existing .venv directory.")
                    print_info(f"Creating virtual environment at: {VENV_PATH} using {system_python_executable}")
                    # Logic to determine cmd_for_venv for venv creation (again for recreation)
                    cmd_for_venv_str_or_list_recreate = []
                    if ' ' in system_python_executable and IS_WINDOWS:
                        cmd_for_venv_str_or_list_recreate = f"{system_python_executable} -m venv {VENV_PATH}"
                        print(f"{Colors.YELLOW}DEBUG_VENV_CREATE (recreate): Running as string (shell=True): {cmd_for_venv_str_or_list_recreate}{Colors.ENDC}")
                    else:
                        cmd_parts_venv_recreate = system_python_executable.split()
                        cmd_for_venv_str_or_list_recreate = cmd_parts_venv_recreate + ['-m', 'venv', VENV_PATH]
                        print(f"{Colors.YELLOW}DEBUG_VENV_CREATE (recreate): Running as list: {cmd_for_venv_str_or_list_recreate}{Colors.ENDC}")

                    subprocess.run(cmd_for_venv_str_or_list_recreate, check=True, shell=IS_WINDOWS)
                    print_success("Virtual environment recreated successfully.")
                except Exception as e:
                    print_error(f"Failed to recreate virtual environment: {e}. Please remove '.venv' manually and re-run.")
                    sys.exit(1)
            else:
                print_info("Proceeding with existing .venv. If issues occur, please remove it manually and re-run.")

    # 3. Re-launch script from venv if not already in it
    print_info(f"Checking if running from venv: sys.prefix='{sys.prefix}', VENV_PATH='{os.path.abspath(VENV_PATH)}'")
    # More robust check for venv activation:
    # On Windows, sys.prefix for a venv is the venv path itself.
    # On Unix, sys.prefix for a venv is also the venv path.
    # sys.base_prefix points to the original Python installation.
    # If they are different, we are in a venv.
    is_in_venv = sys.prefix != sys.base_prefix 
    
    # Additionally, check if the venv is the one we created/expect
    expected_venv_path_abs = os.path.abspath(VENV_PATH)
    current_venv_path_abs = os.path.abspath(sys.prefix)

    if is_in_venv and current_venv_path_abs == expected_venv_path_abs:
        print_success(f"Correct virtual environment ('{expected_venv_path_abs}') is already active.")
        # Set marker for subsequent checks within the same run (e.g. if ensure_python_311_and_venv is called again)
        os.environ[VENV_ACTIVATION_MARKER] = "1"
        # And confirm it's 3.11
        current_version_in_venv = get_python_version(sys.executable)
        if current_version_in_venv and "3.11" in current_version_in_venv:
             print_success(f"Confirmed Python {current_version_in_venv} in active venv.")
             return # All good
        else:
            print_error(f"Script is in the correct venv path, but Python version is {current_version_in_venv}, not 3.11. This is unexpected.")
            print_info("Please delete the .venv folder and re-run the script.")
            sys.exit(1)
    else:
        if is_in_venv:
            print_warning(f"Script is running in a virtual environment ('{current_venv_path_abs}'), but not the expected one ('{expected_venv_path_abs}').")
            print_info("Attempting to re-launch in the correct Suna virtual environment...")
        else:
             print_info("Not running in the Suna virtual environment. Attempting to re-launch...")

        print_info(f"Re-launching setup with Python from: {PYTHON_IN_VENV}")
        
        # Set the marker environment variable before re-launching
        os.environ[VENV_ACTIVATION_MARKER] = "1"
        
        try:
            # sys.argv includes the script name as the first argument.
            # We want to run 'python.exe setup.py install' (or other args)
            args_for_subprocess = [PYTHON_IN_VENV] + sys.argv
            print_info(f"Executing: {' '.join(args_for_subprocess)}")
            
            # For Windows, shell=True might sometimes be needed if PYTHON_IN_VENV has spaces
            # and we are not careful with quoting, but subprocess typically handles this.
            # Pass current environment variables, including the marker.
            process = subprocess.Popen(args_for_subprocess, env=os.environ.copy())
            process.wait() # Wait for the new process to complete
            sys.exit(process.returncode) # Exit with the same code as the child process

        except FileNotFoundError:
            print_error(f"Failed to re-launch: Python executable not found at {PYTHON_IN_VENV}")
            print_info("Ensure the virtual environment was created correctly.")
            sys.exit(1)
        except subprocess.SubprocessError as e:
            print_error(f"Failed to re-launch script in virtual environment: {e}")
            sys.exit(1)

def main():
    # Ensure Python 3.11 and venv are set up before anything else.
    # This function will handle re-launching if necessary.
    ensure_python_311_and_venv()

    state = load_state()
    env_vars = state.get('env_vars', {})

    total_steps = 8
    current_step = 1
    
    print_banner()
    print("This wizard will guide you through setting up Suna, an open-source generalist AI agent.\n")
    
    print_step(current_step, total_steps, "Checking requirements & Environment")
    # check_requirements() will be called, and Python 3.11 check within it should now pass
    # because we are (or will be after re-launch) in the venv.
    check_requirements() # Python check within this should be fine now
    check_docker_running()
    
    if not check_suna_directory():
        print_error("This setup script must be run from the Suna repository root directory.")
        sys.exit(1)
    current_step += 1
    
    # Steps below assume we are now running in the correct Python 3.11 venv
    print_step(current_step, total_steps, "Collecting Supabase information")
    supabase_info = collect_supabase_info(env_vars.get('supabase', {}))
    env_vars['supabase'] = supabase_info
    if 'SUPABASE_URL' in supabase_info: # Keep this for setup_supabase dependency
        os.environ['SUPABASE_URL'] = supabase_info['SUPABASE_URL']
    save_state({'env_vars': env_vars})
    current_step += 1
    
    print_step(current_step, total_steps, "Collecting Daytona information")
    daytona_info = collect_daytona_info(env_vars.get('daytona', {}))
    env_vars['daytona'] = daytona_info
    save_state({'env_vars': env_vars})
    current_step += 1
    
    print_step(current_step, total_steps, "Collecting LLM API keys")
    llm_api_keys = collect_llm_api_keys(env_vars.get('llm', {}))
    env_vars['llm'] = llm_api_keys
    save_state({'env_vars': env_vars})
    current_step += 1
    
    print_step(current_step, total_steps, "Collecting search and web scraping API keys")
    search_api_keys = collect_search_api_keys(env_vars.get('search', {}))
    env_vars['search'] = search_api_keys
    save_state({'env_vars': env_vars})
    current_step += 1
    
    print_step(current_step, total_steps, "Collecting RapidAPI key")
    rapidapi_keys = collect_rapidapi_keys(env_vars.get('rapidapi', {}))
    env_vars['rapidapi'] = rapidapi_keys
    save_state({'env_vars': env_vars})
    current_step += 1
    
    # Setup Supabase database
    supabase_config_to_use = env_vars.get('supabase', {})
    supabase_previously_completed = env_vars.get('supabase_setup_completed', False)
    # setup_supabase will print if skipped and sys.exit on error if not skipped
    setup_supabase(supabase_config_to_use, supabase_previously_completed) 
    
    if not supabase_previously_completed:
        # If setup_supabase ran (wasn't skipped) and didn't exit, it means it succeeded.
        print_info("Marking Supabase setup as completed in state.")
        env_vars['supabase_setup_completed'] = True
        save_state({'env_vars': env_vars})
    current_step += 1
    
    # Install dependencies before starting Suna
    print_step(current_step, total_steps, "Installing dependencies")
    dependencies_previously_installed = env_vars.get('dependencies_installed', False)
    installation_succeeded = install_dependencies(dependencies_previously_installed)

    if not dependencies_previously_installed:
        if installation_succeeded:
            env_vars['dependencies_installed'] = True
            print_info("Marking dependencies as installed in state.")
            save_state({'env_vars': env_vars})
        else:
            print_error("Dependency installation failed. Exiting setup.")
            save_state({'env_vars': env_vars}) # Save other collected info before exiting
            sys.exit(1)
    elif installation_succeeded: # Was skipped and returned True
         print_info("Dependencies installation was skipped as it was previously completed.")
    else: # Skipped but returned False - should not happen
        print_warning("install_dependencies was skipped but did not return True. Check logic.")
        # Still save state as a precaution
        save_state({'env_vars': env_vars})
    
    # Configure environment files with the correct settings before starting
    # env_vars is already populated and saved throughout the steps above.
    print_info("Configuring environment files...")
    configure_backend_env(env_vars, True)  # Always create for Docker first
    configure_frontend_env(env_vars, True)
    
    # Now ask how to start Suna
    print_step(current_step, total_steps, "Starting Suna")
    use_docker = start_suna() # This function might also benefit from env_vars in the future
    
    # Update environment files if needed for non-Docker setup
    if not use_docker:
        print_info("Updating environment files for manual startup...")
        configure_backend_env(env_vars, use_docker)
        configure_frontend_env(env_vars, use_docker)
    
    # Final instructions
    final_instructions(use_docker, env_vars)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nSetup interrupted. You can resume setup anytime by running this script again.")
        sys.exit(1)
