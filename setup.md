# Experimental Environment Setup

> Based on Elektrobit's official tutorial: https://elektrobit.github.io/ebcl_template/main/intro/setup.html

## Overview

The experimental setup uses the **EB corbos Linux template workspace** (v1.6.0), publicly available at `https://github.com/Elektrobit/ebcl_template`. The workspace integrates with Visual Studio Code and includes a pre-configured dev container supporting automated build, deployment, and debugging via Taskfiles.

**Official Support:** Ubuntu 22.04 on x86_64 (amd64) host machines  
**Alternative Support:** Currently, the platform is not suitable for arm64 hosts:
- `lisa-elf-enabler:arm64` is a crucial package for high-integrity applications, currently missing from Elektrobit’s repositories---
---

## Base Setup Steps

### 1. Infrastructure Configuration

**Host Machine:** DEI Network Computer  
**Connection:** `ssh marco@10.3.1.104`  
**Credentials:** Password: `automotive`  
**Remote Access:** MacBook M1 Pro (arm64) using VPN required for off-campus connections


**VM Specifications:**
- OS: Ubuntu 22.04 LTS
- Architecture: x86_64 (amd64)

### 2. VSCode Remote Development Setup

On remote machine (macOS), install:
- Visual Studio Code
- Extensions:
  - Remote - SSH
  - Dev Containers

### 3. Clone Template Workspace

SSH into VM and clone template repository:

```bash
git clone --branch v1.6.0 https://github.com/Elektrobit/ebcl_template
```

Open workspace in VSCode:
1. Connect to VM via Remote-SSH
2. Open `ebcl_sdk.code-workspace`
3. Execute command: **"Reopen in Container"** (Ctrl+Shift+P)

### (Optional) 4. Verify Standard Image Build

This step is just to verify the build of a standard EB corbos Linux image, it should run without problems:

```bash
cd workspace/images/arm64/appdev/qemu/ebcl_1.x
task
```

### 5. Configure EB corbos Linux for Safety Applications

The platform is located in target directory: `workspace/images/arm64/qemu/ebclfsa`

To be able to build the platform image, there are some preliminary steps:
1. Create an Elektrobit account and sign a contract to obtain a free license for “EB corbos Linux for Safety Applications"
2. After 1-2 days, an email should be received, providing an archive with hypervisor files.
3. Extract the hypervisor files to the directory `results/packages/`
4. Make sure to run in a separate terminal the following command: `gen_sign_key && GNUPGHOME=/workspace/gpg-keys/.gnupg gen_app_apt_repo && serve_packages`
   This sets up a local apt repository that serves the hypervisor packages on http://localhost:8000

### 6. Build Image

With all the pre-requisites set up, the demo image is ready to be built:

```bash
cd workspace/images/arm64/qemu/ebclfsa
task qemu
```

After a couple of minutes, the image should run the EB corbos Hypervisor with 2 partitions: a low-integrity Virtual Machine (vm-li) on http://localhost:2222, and a high-integrity VM (vm-hi) on http://localhost:3333.

### (Optional) 7. Test the Demo Application

The demo application is located in `workspace/apps/ebclfsa-demo`, and it tests shared memory communication by using three separate binaries. Two of them are high integrity applications (hi_main and hi_forward) and one is a low integrity application (li_demo). This is used to demonstrate three core concepts: Communication over shared memory between low and high integrity applications; Starting of additional high integrity applications; Communication between high integrity applications.

When the demo image is built, it automatically builds / rebuilds the application, so all changes to the application are always reflected in the image. When the image is started, the high integrity applications are started automatically. 

On the low integrity VM, a shell session is open, and as such, the low-integrity app can be started manually by just typing `li_demo`. It can also be done by using CMake Presets and tasks available in the workspace.
	
With the workspace open on VSCode, there should be a set of bottom ribbons to select: the cmake project/application (`ebclfsa-demo`); the configure preset (`li-app`); and the build preset (`qemu-aarch64`). Once selected, it is a good practice to click the ribbon “Build” to locally test build the application. 

Finally, Ctrl+Shift+P to open up Command Palette:
1. “Tasks: Run Task"
2. “Run app from active build preset (ebclfsa-demo)"

	

## Mount Scenarios

The following steps show how to run the test scenarios, with the example of “scenario1”.

### Clone Scenarios Repository

Clone the repository with the test scenarios:

```bash
git clone https://github.com/MarcoLucas41/eb-corbos-safetyapps-platform-evaluation
```

Copy and paste the scenarios folders into `workspace/apps`. Make sure to include the scenarios folder paths in `workspace/ebcl_sdk.code-workspace`.


### Modify the Image

In `/workspace/images/arm64/qemu/ebclfsa/hi/config_root.sh`:
	comment `chmod +x /hi_main /hi_forward`
    add `chmod +x /speedometer`. This is the executable for the high-integrity app in scenario1.

In `/workspace/images/arm64/qemu/ebclfsa/hv/hv-base.yaml`:
	comment `cmdline: "console=hvc0 ip=192.168.7.100::192.168.7.1:255.255.255.0 root=/dev/vda ro sdk_enable init=/hi_main"`
    add `cmdline: "console=hvc0 ip=192.168.7.100::192.168.7.1:255.255.255.0 root=/dev/vda ro sdk_enable init=/speedometer`

In `/workspace/images/arm64/qemu/ebclfsa/Taskfile.yml`:
	comment `demo_app: /workspace/apps/ebclfsa-demo`
  	add `demo_app: /workspace/apps/scenario1`

These changes are sufficient to be able to automatically build and run the high-integrity app in the high-integrity VM. 

### Deploying Applications

In order to deploy the low-integrity applications, set a CMake project (for example: apps/scenario1) and choose the configure preset for that app (qemu-aarch64).
When building/deploying/running, use tasks that are scoped to the app, e.g.:
    Cmake build app from active build preset (scenario1)
    Deploy app from active build preset (scenario1)
    Run app from active build preset (scenario1)
and do not use the (common) variants. 



