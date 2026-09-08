//go:build windows

package main

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

func replaceFile(from, to string) error {
	kernel := syscall.NewLazyDLL("kernel32.dll")
	proc := kernel.NewProc("MoveFileExW")
	a, e := syscall.UTF16PtrFromString(from)
	if e != nil {
		return e
	}
	b, e := syscall.UTF16PtrFromString(to)
	if e != nil {
		return e
	}
	r, _, err := proc.Call(uintptr(unsafe.Pointer(a)), uintptr(unsafe.Pointer(b)), uintptr(0x1|0x8))
	if r == 0 {
		return err
	}
	return nil
}
func configureCommand(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x08000000 | 0x00000200, HideWindow: true}
}
func terminateCommand(c *exec.Cmd) error {
	if c.Process == nil {
		return os.ErrProcessDone
	}
	k := exec.Command("taskkill.exe", "/PID", fmt.Sprint(c.Process.Pid), "/T", "/F")
	configureCommand(k)
	if e := k.Run(); e != nil {
		return c.Process.Kill()
	}
	return nil
}
func openBrowser(u string) {
	if !validLocalURL(u) {
		return
	}
	p := syscall.NewLazyDLL("shell32.dll").NewProc("ShellExecuteW")
	op, _ := syscall.UTF16PtrFromString("open")
	url, _ := syscall.UTF16PtrFromString(u)
	p.Call(0, uintptr(unsafe.Pointer(op)), uintptr(unsafe.Pointer(url)), 0, 0, 1)
}
func nativeError(s string) {
	p := syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW")
	text, _ := syscall.UTF16PtrFromString(s)
	title, _ := syscall.UTF16PtrFromString("SoulX Studio")
	p.Call(0, uintptr(unsafe.Pointer(text)), uintptr(unsafe.Pointer(title)), 0x10)
}
func psQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }
func installShortcut(root string) error {
	// Powershell is used only for the OS shortcut COM API, not executing downloaded scripts.
	// No ExecutionPolicy override, registry edit or administrator elevation.
	exe, e := os.Executable()
	if e != nil {
		return e
	}
	dest := filepath.Join(root, "SoulXStudio-"+Version+".exe")
	if !strings.EqualFold(exe, dest) {
		in, e := os.Open(exe)
		if e != nil {
			return e
		}
		defer in.Close()
		tmp := dest + ".copy"
		out, e := os.Create(tmp)
		if e != nil {
			return e
		}
		_, e = io.Copy(out, in)
		ce := out.Close()
		if e != nil {
			return e
		}
		if ce != nil {
			return ce
		}
		if e = replaceFile(tmp, dest); e != nil {
			return e
		}
	}
	script := `$ErrorActionPreference='Stop'; $w=New-Object -ComObject WScript.Shell; $d=[Environment]::GetFolderPath('Programs'); $l=$w.CreateShortcut((Join-Path $d 'SoulX Studio.lnk')); $l.TargetPath=` + psQuote(dest) + `; $l.WorkingDirectory=` + psQuote(root) + `; $l.Description='SoulX local voice studio'; $l.Save()`
	c := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script)
	configureCommand(c)
	return c.Run()
}
