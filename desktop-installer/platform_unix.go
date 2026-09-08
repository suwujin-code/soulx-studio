//go:build !windows

package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"syscall"
)

func replaceFile(from, to string) error { return os.Rename(from, to) }
func configureCommand(c *exec.Cmd)      { c.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} }
func terminateCommand(c *exec.Cmd) error {
	if c.Process == nil {
		return os.ErrProcessDone
	}
	e := syscall.Kill(-c.Process.Pid, syscall.SIGKILL)
	if errors.Is(e, syscall.ESRCH) {
		return os.ErrProcessDone
	}
	return e
}
func openBrowser(u string) {
	if !validLocalURL(u) {
		return
	}
	name := "xdg-open"
	if runtime.GOOS == "darwin" {
		name = "open"
	}
	c := exec.Command(name, u)
	_ = c.Start()
	go func() { _ = c.Wait() }()
}
func nativeError(s string) {
	fmt.Fprintln(os.Stderr, s)
	if runtime.GOOS == "darwin" {
		_ = exec.Command("osascript", "-e", "on run argv\n display alert \"SoulX Studio\" message (item 1 of argv)\nend run", s).Run()
	}
}
func installShortcut(root string) error { return nil } // macOS user places the signed/preview .app in Applications.
