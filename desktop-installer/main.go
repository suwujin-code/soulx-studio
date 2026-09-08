// SoulX local installer and service supervisor. Uses only the Go standard library.
// Network access is opt-in from the local setup UI; no system Python or shell installer.
package main

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

const Version = "3.0.1-preview.1"
const UVVersion = "0.10.0"

//go:embed payload.zip
var payload []byte

//go:embed setup.html
var setupHTML string

var downloadClient = &http.Client{Timeout: 20 * time.Minute, CheckRedirect: func(req *http.Request, via []*http.Request) error {
	if len(via) > 8 {
		return errors.New("too many redirects")
	}
	if req.URL.Scheme != "https" {
		return errors.New("non-HTTPS redirect refused")
	}
	return nil
}}

type Installed struct {
	Version     string `json:"version"`
	Source      string `json:"source"`
	Python      string `json:"python"`
	Profile     string `json:"profile"`
	ModelDir    string `json:"model_dir"`
	InstalledAt string `json:"installed_at"`
}
type InstallRequest struct {
	Profile       string `json:"profile"`
	ModelDir      string `json:"model_dir"`
	DownloadModel bool   `json:"download_model"`
	Consent       bool   `json:"consent"`
}
type State struct {
	Version        string   `json:"version"`
	Platform       string   `json:"platform"`
	Root           string   `json:"root"`
	Stage          string   `json:"stage"`
	Error          string   `json:"error"`
	Busy           bool     `json:"busy"`
	Installed      bool     `json:"installed"`
	ServiceRunning bool     `json:"service_running"`
	ServiceURL     string   `json:"service_url"`
	SpeechOption   bool     `json:"speech_option"`
	CUDAOption     bool     `json:"cuda_option"`
	Logs           []string `json:"logs"`
}
type App struct {
	suppressBrowser        bool
	mu                     sync.Mutex
	state                  State
	root, token, address   string
	servicePort            int
	installed              *Installed
	cancel                 context.CancelFunc
	service                *exec.Cmd
	serviceDone            chan struct{}
	controlToken, instance string
	logfile                *os.File
	exit                   func()
}

func secret() string {
	b := make([]byte, 24)
	if _, e := rand.Read(b); e != nil {
		panic(e)
	}
	return hex.EncodeToString(b)
}
func defaultRoot(goos, home, localAppData, xdg string) (string, error) {
	if home == "" {
		return "", errors.New("cannot locate user home directory")
	}
	switch goos {
	case "windows":
		if localAppData == "" {
			localAppData = filepath.Join(home, "AppData", "Local")
		}
		return filepath.Join(localAppData, "SoulXStudio"), nil
	case "darwin":
		return filepath.Join(home, "Library", "Application Support", "SoulXStudio"), nil
	default:
		if xdg == "" {
			xdg = filepath.Join(home, ".local", "share")
		}
		return filepath.Join(xdg, "SoulXStudio"), nil
	}
}
func newApp(root string, port int) (*App, error) {
	var err error
	root, err = filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	if err = os.MkdirAll(filepath.Join(root, "logs"), 0700); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(filepath.Join(root, "logs", "installer.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
	if err != nil {
		return nil, err
	}
	a := &App{root: root, token: secret(), servicePort: port, logfile: f}
	a.state = State{Version: Version, Platform: runtime.GOOS + " / " + runtime.GOARCH, Root: root, Stage: "待安装", Logs: []string{},
		SpeechOption: (runtime.GOOS == "darwin" && runtime.GOARCH == "arm64") || (runtime.GOOS == "windows" && runtime.GOARCH == "amd64") || runtime.GOOS == "linux",
		CUDAOption:   runtime.GOOS == "windows" && runtime.GOARCH == "amd64"}
	a.loadInstalled()
	return a, nil
}
func (a *App) snapshot() State {
	a.mu.Lock()
	defer a.mu.Unlock()
	s := a.state
	s.Logs = append([]string{}, s.Logs...)
	return s
}
func (a *App) log(s string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	s = strings.TrimSpace(s)
	if s == "" {
		return
	}
	line := time.Now().Format("15:04:05") + " " + s
	a.state.Logs = append(a.state.Logs, line)
	if len(a.state.Logs) > 180 {
		a.state.Logs = a.state.Logs[len(a.state.Logs)-180:]
	}
	if a.logfile != nil {
		fmt.Fprintln(a.logfile, line)
	}
}
func (a *App) stage(s string) { a.mu.Lock(); a.state.Stage = s; a.mu.Unlock(); a.log(s) }
func (a *App) loadInstalled() {
	b, e := os.ReadFile(filepath.Join(a.root, "installed.json"))
	if e != nil {
		return
	}
	var c Installed
	if json.Unmarshal(b, &c) != nil || c.Python == "" || c.Source == "" || !inside(a.root, c.Python) || !inside(a.root, c.Source) {
		a.log("忽略无效安装记录；原数据未修改。")
		return
	}
	if _, e = os.Stat(c.Python); e != nil {
		return
	}
	if _, e = os.Stat(filepath.Join(c.Source, "desktop_server.py")); e != nil {
		return
	}
	a.installed = &c
	a.state.Installed = true
	a.state.Stage = "已安装，可启动"
}
func inside(root, path string) bool {
	r, e := filepath.Rel(root, path)
	return e == nil && r != ".." && !strings.HasPrefix(r, ".."+string(filepath.Separator)) && !filepath.IsAbs(r)
}
func writeJSON(path string, v any) error {
	b, e := json.MarshalIndent(v, "", "  ")
	if e != nil {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(path), ".write-*")
	if e != nil {
		return e
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if e = f.Chmod(0600); e != nil {
		f.Close()
		return e
	}
	if _, e = f.Write(b); e != nil {
		f.Close()
		return e
	}
	if e = f.Sync(); e != nil {
		f.Close()
		return e
	}
	if e = f.Close(); e != nil {
		return e
	}
	return replaceFile(tmp, path)
}
func hashBytes(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }
func safeArchivePath(root, name string) (string, error) {
	if name == "" || strings.ContainsAny(name, "\\:\x00") || strings.HasPrefix(name, "/") {
		return "", errors.New("unsafe archive path: " + name)
	}
	parts := strings.Split(strings.TrimSuffix(name, "/"), "/")
	for _, p := range parts {
		if p == ".." || p == "." || p == "" || strings.HasSuffix(p, ".") || strings.HasSuffix(p, " ") {
			return "", errors.New("invalid archive component")
		}
	}
	target := filepath.Join(root, filepath.FromSlash(name))
	if !inside(root, target) {
		return "", errors.New("archive path escapes destination")
	}
	return target, nil
}
func extractPayload(blob []byte, root string) error {
	z, e := zip.NewReader(bytes.NewReader(blob), int64(len(blob)))
	if e != nil {
		return e
	}
	var total uint64
	seen := map[string]bool{}
	for _, f := range z.File {
		target, e := safeArchivePath(root, f.Name)
		if e != nil {
			return e
		}
		key := strings.ToLower(f.Name)
		if seen[key] {
			return errors.New("duplicate archive path")
		}
		seen[key] = true
		if f.Mode()&os.ModeSymlink != 0 {
			return errors.New("symlink entries not accepted")
		}
		total += f.UncompressedSize64
		if total > 128<<20 || f.UncompressedSize64 > 32<<20 {
			return errors.New("payload exceeds limits")
		}
		if f.FileInfo().IsDir() {
			if e = os.MkdirAll(target, 0700); e != nil {
				return e
			}
			continue
		}
		if e = os.MkdirAll(filepath.Dir(target), 0700); e != nil {
			return e
		}
		in, e := f.Open()
		if e != nil {
			return e
		}
		out, e := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if e != nil {
			in.Close()
			return e
		}
		n, ce := io.Copy(out, io.LimitReader(in, int64(f.UncompressedSize64)+1))
		in.Close()
		closeErr := out.Close()
		if ce != nil {
			return ce
		}
		if closeErr != nil {
			return closeErr
		}
		if n != int64(f.UncompressedSize64) {
			return errors.New("payload size mismatch")
		}
	}
	return nil
}
func (a *App) source() (string, error) {
	dir := filepath.Join(a.root, "releases", Version+"-"+hashBytes(payload)[:12])
	marker := filepath.Join(dir, ".payload.sha256")
	if b, e := os.ReadFile(marker); e == nil && string(b) == hashBytes(payload) {
		return dir, nil
	}
	if e := os.MkdirAll(filepath.Dir(dir), 0700); e != nil {
		return "", e
	}
	stage, e := os.MkdirTemp(filepath.Dir(dir), ".extract-")
	if e != nil {
		return "", e
	}
	defer os.RemoveAll(stage)
	if e = extractPayload(payload, stage); e != nil {
		return "", e
	}
	if e = os.WriteFile(filepath.Join(stage, ".payload.sha256"), []byte(hashBytes(payload)), 0600); e != nil {
		return "", e
	}
	if _, e = os.Stat(dir); e == nil {
		return "", errors.New("existing release is incomplete; refusing to overwrite it")
	}
	if e = os.Rename(stage, dir); e != nil {
		return "", e
	}
	return dir, nil
}
func uvArtifact(goos, arch string) (string, error) {
	switch goos + "/" + arch {
	case "darwin/arm64":
		return "uv-aarch64-apple-darwin.tar.gz", nil
	case "darwin/amd64":
		return "uv-x86_64-apple-darwin.tar.gz", nil
	case "windows/amd64":
		return "uv-x86_64-pc-windows-msvc.zip", nil
	case "linux/amd64":
		return "uv-x86_64-unknown-linux-gnu.tar.gz", nil
	default:
		return "", fmt.Errorf("unsupported runtime platform: %s/%s", goos, arch)
	}
}
func fetch(ctx context.Context, client *http.Client, address string, max int64) ([]byte, error) {
	req, e := http.NewRequestWithContext(ctx, "GET", address, nil)
	if e != nil {
		return nil, e
	}
	req.Header.Set("User-Agent", "SoulXStudio/"+Version)
	r, e := client.Do(req)
	if e != nil {
		return nil, e
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return nil, fmt.Errorf("download HTTP %d", r.StatusCode)
	}
	b, e := io.ReadAll(io.LimitReader(r.Body, max+1))
	if e != nil {
		return nil, e
	}
	if int64(len(b)) > max {
		return nil, errors.New("download exceeds allowed size")
	}
	return b, nil
}
func checksum(text []byte) (string, error) {
	fields := strings.Fields(string(text))
	if len(fields) < 1 || len(fields[0]) != 64 {
		return "", errors.New("invalid SHA-256 document")
	}
	if _, e := hex.DecodeString(fields[0]); e != nil {
		return "", e
	}
	return strings.ToLower(fields[0]), nil
}
func executableFromArchive(blob []byte, filename string) ([]byte, error) {
	name := "uv"
	if strings.HasSuffix(filename, ".zip") {
		name = "uv.exe"
		z, e := zip.NewReader(bytes.NewReader(blob), int64(len(blob)))
		if e != nil {
			return nil, e
		}
		for _, f := range z.File {
			if filepath.Base(f.Name) == name && f.Mode()&os.ModeSymlink == 0 && f.UncompressedSize64 <= 100<<20 {
				r, e := f.Open()
				if e != nil {
					return nil, e
				}
				b, e := io.ReadAll(io.LimitReader(r, 100<<20))
				r.Close()
				return b, e
			}
		}
	} else {
		gz, e := gzip.NewReader(bytes.NewReader(blob))
		if e != nil {
			return nil, e
		}
		defer gz.Close()
		tr := tar.NewReader(gz)
		for {
			h, e := tr.Next()
			if e == io.EOF {
				break
			}
			if e != nil {
				return nil, e
			}
			if filepath.Base(h.Name) == name && h.Typeflag == tar.TypeReg && h.Size <= 100<<20 {
				return io.ReadAll(io.LimitReader(tr, h.Size))
			}
		}
	}
	return nil, errors.New("uv executable missing from official archive")
}
func (a *App) ensureUV(ctx context.Context) (string, error) {
	dir := filepath.Join(a.root, "runtime", "uv", UVVersion)
	name := "uv"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	path := filepath.Join(dir, name)
	if b, e := os.ReadFile(path); e == nil {
		receipt, e := os.ReadFile(path + ".sha256")
		if e == nil && string(receipt) == hashBytes(b) {
			return path, nil
		}
	}
	artifact, e := uvArtifact(runtime.GOOS, runtime.GOARCH)
	if e != nil {
		return "", e
	}
	base := "https://github.com/astral-sh/uv/releases/download/" + UVVersion + "/" + artifact
	a.stage("下载独立运行工具（官方固定版本）")
	sumDoc, e := fetch(ctx, downloadClient, base+".sha256", 4096)
	if e != nil {
		return "", fmt.Errorf("无法取得官方校验文件；请检查网络（未继续安装）：%w", e)
	}
	sum, e := checksum(sumDoc)
	if e != nil {
		return "", e
	}
	blob, e := fetch(ctx, downloadClient, base, 100<<20)
	if e != nil {
		return "", e
	}
	if hashBytes(blob) != sum {
		return "", errors.New("官方运行工具 SHA-256 不匹配，已拒绝执行")
	}
	bin, e := executableFromArchive(blob, artifact)
	if e != nil {
		return "", e
	}
	if e = os.MkdirAll(dir, 0700); e != nil {
		return "", e
	}
	tmp := path + ".download"
	if e = os.WriteFile(tmp, bin, 0700); e != nil {
		return "", e
	}
	if e = replaceFile(tmp, path); e != nil {
		return "", e
	}
	if e = os.WriteFile(path+".sha256", []byte(hashBytes(bin)), 0600); e != nil {
		return "", e
	}
	a.log("运行工具已通过官方归档 SHA-256 校验")
	return path, nil
}
func (a *App) environment() []string {
	env := append([]string{}, os.Environ()...)
	kv := map[string]string{"UV_PYTHON_INSTALL_DIR": filepath.Join(a.root, "runtime", "python"), "UV_CACHE_DIR": filepath.Join(a.root, "cache", "uv"), "UV_PYTHON_INSTALL_REGISTRY": "false", "UV_NO_PROGRESS": "1", "UV_DEFAULT_INDEX": "https://pypi.org/simple", "XDG_CACHE_HOME": filepath.Join(a.root, "cache"), "HF_HOME": filepath.Join(a.root, "cache", "huggingface"), "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "PYTORCH_ENABLE_MPS_FALLBACK": "1"}
	for key, value := range kv {
		prefix := key + "="
		filtered := env[:0]
		for _, e := range env {
			if !strings.HasPrefix(e, prefix) {
				filtered = append(filtered, e)
			}
		}
		env = append(filtered, prefix+value)
	}
	return env
}

type logWriter struct{ app *App }

func (w logWriter) Write(p []byte) (int, error) {
	for _, line := range strings.Split(strings.ReplaceAll(string(p), "\r", "\n"), "\n") {
		w.app.log(line)
	}
	return len(p), nil
}
func (a *App) command(ctx context.Context, dir, exe string, args ...string) error {
	cmd := exec.CommandContext(ctx, exe, args...)
	cmd.Dir = dir
	cmd.Env = a.environment()
	configureCommand(cmd)
	cmd.Cancel = func() error { return terminateCommand(cmd) }
	cmd.WaitDelay = 10 * time.Second
	cmd.Stdout = logWriter{a}
	cmd.Stderr = logWriter{a}
	if e := cmd.Run(); e != nil {
		if ctx.Err() != nil {
			return errors.New("安装已取消；已完成的工作区和旧安装均保留")
		}
		return fmt.Errorf("安装步骤失败：%w", e)
	}
	return nil
}
func validateRequest(req InstallRequest, goos, arch string) error {
	if !req.Consent {
		return errors.New("必须明确同意下载组件")
	}
	if req.Profile != "desktop" && req.Profile != "speech" && req.Profile != "speech-cuda" {
		return errors.New("invalid installation profile")
	}
	if req.Profile != "desktop" && goos == "darwin" && arch != "arm64" {
		return errors.New("Intel Mac 本版只开放工作台；本机语音依赖尚未验证")
	}
	if req.Profile == "speech-cuda" && !(goos == "windows" && arch == "amd64") {
		return errors.New("CUDA profile is only offered on Windows x64")
	}
	if _, e := uvArtifact(goos, arch); e != nil {
		return e
	}
	if req.ModelDir != "" && !filepath.IsAbs(req.ModelDir) {
		return errors.New("模型目录必须是完整绝对路径")
	}
	return nil
}
func (a *App) install(ctx context.Context, req InstallRequest) error {
	if e := validateRequest(req, runtime.GOOS, runtime.GOARCH); e != nil {
		return e
	}
	a.stage("展开工作台代码；不覆盖项目数据")
	source, e := a.source()
	if e != nil {
		return e
	}
	uv, e := a.ensureUV(ctx)
	if e != nil {
		return e
	}
	if e = os.MkdirAll(filepath.Join(a.root, "environments"), 0700); e != nil {
		return e
	}
	// Each attempt has its own immutable environment. Failed updates never destroy the old one.
	envDir := filepath.Join(a.root, "environments", Version+"-"+req.Profile+"-"+secret()[:8])
	a.stage("自动准备 Python 3.11 与独立环境")
	if e = a.command(ctx, source, uv, "--no-config", "venv", "--python", "3.11", "--managed-python", envDir); e != nil {
		return e
	}
	py := filepath.Join(envDir, "bin", "python")
	if runtime.GOOS == "windows" {
		py = filepath.Join(envDir, "Scripts", "python.exe")
	}
	a.stage("安装工作台与音频文件组件")
	if e = a.command(ctx, source, uv, "--no-config", "pip", "install", "--python", py, "--only-binary", ":all:", "-r", filepath.Join(source, "requirements.desktop.txt")); e != nil {
		return e
	}
	if req.Profile != "desktop" {
		a.stage("安装配套 PyTorch 与真实语音依赖")
		args := []string{"--no-config", "pip", "install", "--python", py, "torch==2.7.1", "torchaudio==2.7.1"}
		if runtime.GOOS != "darwin" {
			index := "https://download.pytorch.org/whl/cpu"
			if req.Profile == "speech-cuda" {
				index = "https://download.pytorch.org/whl/cu126"
			}
			args = append(args, "--index-url", index)
		}
		if e = a.command(ctx, source, uv, args...); e != nil {
			return e
		}
		if e = a.command(ctx, source, uv, "--no-config", "pip", "install", "--python", py, "--only-binary", ":all:", "-r", filepath.Join(source, "requirements.speech-desktop.txt")); e != nil {
			return e
		}
		if e = a.command(ctx, source, py, "-c", "import torch, torchaudio, torchaudio.compliance.kaldi, transformers, onnxruntime, s3tokenizer, scipy; print('Speech libraries imported; model NOT loaded'); print('CUDA available:',torch.cuda.is_available())"); e != nil {
			return e
		}
		if req.Profile == "speech-cuda" {
			if e = a.command(ctx, source, py, "-c", "import torch; assert torch.cuda.is_available(), 'CUDA not available; choose CPU profile or install a compatible NVIDIA driver yourself'"); e != nil {
				return e
			}
		}
		a.stage("校验主模型与辅助语音资源")
		if req.ModelDir == "" {
			req.ModelDir = filepath.Join(a.root, "models", "SoulX-Podcast-1.7B-dialect")
		}
		args = []string{filepath.Join(source, "scripts", "prepare_desktop_model.py"), "--model-dir", req.ModelDir, "--cache-dir", filepath.Join(a.root, "cache"), "--report", filepath.Join(a.root, "logs", "model-verification.json")}
		if req.DownloadModel {
			args = append(args, "--download")
		}
		if e = a.command(ctx, source, py, args...); e != nil {
			return e
		}
	}
	a.stage("检查服务导入与依赖一致性")
	if e = a.command(ctx, source, uv, "--no-config", "pip", "check", "--python", py); e != nil {
		return e
	}
	if e = a.command(ctx, source, py, "-c", "import fastapi,uvicorn,soundfile; from run_studio import build_app; print('Desktop imports OK. No speech inference performed.')"); e != nil {
		return e
	}
	c := Installed{Version: Version, Source: source, Python: py, Profile: req.Profile, ModelDir: req.ModelDir, InstalledAt: time.Now().UTC().Format(time.RFC3339)}
	a.mu.Lock()
	previous := a.installed
	a.mu.Unlock()
	if e = writeJSON(filepath.Join(a.root, "installed.json"), c); e != nil {
		return e
	}
	a.mu.Lock()
	a.installed = &c
	a.state.Installed = true
	a.mu.Unlock()
	a.stage("启动本机服务")
	if e = a.startService(); e != nil {
		if previous != nil {
			_ = writeJSON(filepath.Join(a.root, "installed.json"), previous)
		} else {
			_ = os.Remove(filepath.Join(a.root, "installed.json"))
		}
		a.mu.Lock()
		a.installed = previous
		a.state.Installed = previous != nil
		a.mu.Unlock()
		return e
	}
	if e = installShortcut(a.root); e != nil {
		a.log("快捷方式未创建，可再次打开此应用启动：" + e.Error())
	}
	a.stage("工作台已启动；真实粤语仍需首次试听验收")
	return nil
}
func (a *App) startService() error {
	a.mu.Lock()
	if a.service != nil {
		a.mu.Unlock()
		return errors.New("service already started")
	}
	c := a.installed
	if c == nil {
		a.mu.Unlock()
		return errors.New("请先安装工作台")
	}
	a.mu.Unlock()
	test, e := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", a.servicePort))
	if e != nil {
		return errors.New("工作台端口被占用；未关闭或冒充其他进程，请退出原工作台后重试")
	}
	test.Close()
	control, instance := secret(), secret()
	cmd := exec.Command(c.Python, filepath.Join(c.Source, "desktop_server.py"), "--port", fmt.Sprint(a.servicePort), "--data-dir", filepath.Join(a.root, "workspace"))
	cmd.Dir = c.Source
	cmd.Env = append(a.environment(), "SOULX_CONTROL_TOKEN="+control, "SOULX_DESKTOP_INSTANCE="+instance, "SOULX_DATA_DIR="+filepath.Join(a.root, "workspace"), "SOULX_PROJECT_ROOT="+c.Source, "SOULX_DRY_RUN=false", "LLM_ENGINE=hf")
	model := c.ModelDir
	if model == "" {
		model = filepath.Join(a.root, "models", "SoulX-Podcast-1.7B-dialect")
	}
	cmd.Env = append(cmd.Env, "MODEL_PATH="+model)
	if runtime.GOOS == "windows" {
		dev := "cpu"
		if c.Profile == "speech-cuda" {
			dev = "cuda"
		}
		cmd.Env = append(cmd.Env, "SOULX_DEVICE="+dev)
	} else {
		cmd.Env = append(cmd.Env, "SOULX_DEVICE=auto")
	}
	configureCommand(cmd)
	cmd.Stdout = logWriter{a}
	cmd.Stderr = logWriter{a}
	if e = cmd.Start(); e != nil {
		return e
	}
	done := make(chan struct{})
	a.mu.Lock()
	a.service = cmd
	a.serviceDone = done
	a.controlToken = control
	a.instance = instance
	a.mu.Unlock()
	go func() {
		e := cmd.Wait()
		a.mu.Lock()
		a.service = nil
		a.state.ServiceRunning = false
		close(done)
		a.mu.Unlock()
		if e != nil {
			a.log("工作台进程退出：" + e.Error())
		} else {
			a.log("工作台已停止，数据保留")
		}
	}()
	deadline := time.Now().Add(40 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-done:
			return errors.New("工作台未通过启动检查，请查看日志")
		default:
		}
		request, _ := http.NewRequest("GET", fmt.Sprintf("http://127.0.0.1:%d/api/desktop/instance", a.servicePort), nil)
		request.Header.Set("X-SoulX-Control", control)
		r, e := (&http.Client{Timeout: 700 * time.Millisecond}).Do(request)
		if e == nil {
			var identity struct {
				Instance string `json:"instance"`
			}
			de := json.NewDecoder(r.Body).Decode(&identity)
			r.Body.Close()
			if r.StatusCode == 200 && de == nil && identity.Instance == instance {
				a.mu.Lock()
				a.state.ServiceRunning = true
				a.state.ServiceURL = fmt.Sprintf("http://127.0.0.1:%d/app/", a.servicePort)
				u := a.state.ServiceURL
				a.mu.Unlock()
				if !a.suppressBrowser {
					openBrowser(u)
				}
				return nil
			}
		}
		time.Sleep(200 * time.Millisecond)
	}
	_ = terminateCommand(cmd)
	select {
	case <-done:
	case <-time.After(12 * time.Second):
	}
	return errors.New("工作台启动检查超时；没有显示假成功，请检查日志")
}
func (a *App) stopService() error {
	a.mu.Lock()
	cmd, done, token := a.service, a.serviceDone, a.controlToken
	a.mu.Unlock()
	if cmd == nil {
		return nil
	}
	req, _ := http.NewRequest("POST", fmt.Sprintf("http://127.0.0.1:%d/api/desktop/shutdown", a.servicePort), bytes.NewReader([]byte("{}")))
	req.Header.Set("X-SoulX-Control", token)
	req.Header.Set("Content-Type", "application/json")
	r, e := (&http.Client{Timeout: 5 * time.Second}).Do(req)
	if e != nil {
		return e
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return errors.New("服务仍在执行任务或拒绝停止。请在工作台处理任务后重试；不会强行删除作品")
	}
	select {
	case <-done:
		a.stage("服务已停止；项目与队列记录保留")
		return nil
	case <-time.After(15 * time.Second):
		return errors.New("已请求停止，进程尚未退出，请稍后重试")
	}
}
func (a *App) begin(stage string, work func(context.Context) error) error {
	a.mu.Lock()
	if a.state.Busy {
		a.mu.Unlock()
		return errors.New("已有操作在执行")
	}
	ctx, cancel := context.WithCancel(context.Background())
	a.cancel = cancel
	a.state.Busy = true
	a.state.Error = ""
	a.state.Stage = stage
	a.mu.Unlock()
	go func() {
		e := work(ctx)
		cancel()
		a.mu.Lock()
		a.cancel = nil
		a.state.Busy = false
		if e != nil {
			a.state.Error = e.Error()
			a.state.Stage = "需要处理"
		}
		a.mu.Unlock()
		if e != nil {
			a.log(e.Error())
		}
	}()
	return nil
}
func jsonResponse(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
func (a *App) handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Frame-Options", "DENY")
		if r.Host != a.address {
			http.Error(w, "Invalid local host", 403)
			return
		}
		if o := r.Header.Get("Origin"); o != "" && o != "http://"+a.address {
			http.Error(w, "Invalid origin", 403)
			return
		}
		if r.URL.Path == "/ping" && r.Method == "GET" {
			jsonResponse(w, 200, map[string]string{"product": "SoulXStudio Installer", "version": Version})
			return
		}
		if r.URL.Path == "/" && r.Method == "GET" {
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			w.Header().Set("Cache-Control", "no-store")
			io.WriteString(w, strings.ReplaceAll(setupHTML, "__CSRF_TOKEN__", a.token))
			return
		}
		if !strings.HasPrefix(r.URL.Path, "/api/") {
			http.NotFound(w, r)
			return
		}
		if subtle.ConstantTimeCompare([]byte(r.Header.Get("X-SoulX-Token")), []byte(a.token)) != 1 {
			jsonResponse(w, 403, map[string]string{"error": "Invalid local action token"})
			return
		}
		action := strings.TrimPrefix(r.URL.Path, "/api/")
		if action == "state" && r.Method == "GET" {
			jsonResponse(w, 200, a.snapshot())
			return
		}
		if r.Method != "POST" {
			jsonResponse(w, 405, map[string]string{"error": "POST required"})
			return
		}
		var err error
		switch action {
		case "install":
			a.mu.Lock()
			active := a.service != nil
			a.mu.Unlock()
			if active {
				err = errors.New("请先停止工作台服务，再更新安装")
				break
			}
			var body InstallRequest
			r.Body = http.MaxBytesReader(w, r.Body, 65536)
			d := json.NewDecoder(r.Body)
			d.DisallowUnknownFields()
			if err = d.Decode(&body); err == nil {
				err = validateRequest(body, runtime.GOOS, runtime.GOARCH)
			}
			if err == nil {
				err = a.begin("正在准备安装", func(ctx context.Context) error { return a.install(ctx, body) })
			}
		case "start":
			err = a.begin("正在启动本机服务", func(context.Context) error {
				e := a.startService()
				if e == nil {
					a.stage("工作台已启动")
				}
				return e
			})
		case "stop":
			err = a.begin("正在请求安全停止", func(context.Context) error { return a.stopService() })
		case "cancel":
			a.mu.Lock()
			if a.cancel != nil {
				a.cancel()
			}
			a.mu.Unlock()
		case "exit":
			a.mu.Lock()
			active := a.state.Busy || a.service != nil
			a.mu.Unlock()
			if active {
				err = errors.New("请先完成/取消安装，并停止工作台服务")
			} else {
				jsonResponse(w, 200, map[string]bool{"ok": true})
				go func() {
					time.Sleep(400 * time.Millisecond)
					if a.exit != nil {
						a.exit()
					}
				}()
				return
			}
		default:
			jsonResponse(w, 404, map[string]string{"error": "unknown action"})
			return
		}
		if err != nil {
			jsonResponse(w, 409, map[string]string{"error": err.Error()})
			return
		}
		jsonResponse(w, 202, map[string]bool{"accepted": true})
	})
}
func main() {
	rootArg := flag.String("root", "", "Custom user-owned installation root (testing/portable use)")
	port := flag.Int("port", 18779, "Local setup centre port")
	servicePort := flag.Int("service-port", 18781, "Local workbench port")
	noBrowser := flag.Bool("no-browser", false, "Do not open browser")
	selfTest := flag.Bool("self-test", false, "Verify embedded payload without installing or network")
	setupOnly := flag.Bool("setup", false, "Do not automatically start an installed workbench")
	flag.Parse()
	if *selfTest {
		tmp, e := os.MkdirTemp("", "soulx-payload-check-")
		if e != nil {
			log.Fatal(e)
		}
		defer os.RemoveAll(tmp)
		if e = extractPayload(payload, tmp); e != nil {
			log.Fatal(e)
		}
		if _, e = os.Stat(filepath.Join(tmp, "desktop_server.py")); e != nil {
			log.Fatal(e)
		}
		fmt.Printf("PASS native=%s/%s payload_sha256=%s version=%s\n", runtime.GOOS, runtime.GOARCH, hashBytes(payload), Version)
		return
	}
	root := *rootArg
	if root == "" {
		home, e := os.UserHomeDir()
		if e != nil {
			log.Fatal(e)
		}
		root, e = defaultRoot(runtime.GOOS, home, os.Getenv("LOCALAPPDATA"), os.Getenv("XDG_DATA_HOME"))
		if e != nil {
			log.Fatal(e)
		}
	}
	a, e := newApp(root, *servicePort)
	if e != nil {
		nativeError("无法创建用户安装目录：" + e.Error())
		return
	}
	defer a.logfile.Close()
	ln, e := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", *port))
	if e != nil {
		address := fmt.Sprintf("http://127.0.0.1:%d", *port)
		r, err := (&http.Client{Timeout: time.Second}).Get(address + "/ping")
		if err == nil {
			defer r.Body.Close()
			var x map[string]string
			_ = json.NewDecoder(r.Body).Decode(&x)
			if x["product"] == "SoulXStudio Installer" {
				openBrowser(address)
				return
			}
		}
		nativeError("安装中心端口被其他程序占用；没有终止其他进程。" + e.Error())
		return
	}
	a.suppressBrowser = *noBrowser
	a.address = ln.Addr().String()
	srv := &http.Server{Handler: a.handler(), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second}
	a.exit = func() { _ = srv.Close() }
	fmt.Println("SoulX setup: http://" + a.address)
	a.log("安装中心已启动；等待用户选择。不自动下载模型。")
	if !*noBrowser {
		go func() { time.Sleep(150 * time.Millisecond); openBrowser("http://" + a.address) }()
	}
	if a.installed != nil && !*setupOnly {
		_ = a.begin("正在启动已安装工作台", func(context.Context) error {
			e := a.startService()
			if e == nil {
				a.stage("工作台已启动")
			}
			return e
		})
	}
	if e = srv.Serve(ln); e != nil && !errors.Is(e, http.ErrServerClosed) {
		a.log(e.Error())
	}
}

// URL validation is deliberately separate for unit tests.
func validLocalURL(raw string) bool {
	u, e := url.Parse(raw)
	return e == nil && u.Scheme == "http" && u.Hostname() == "127.0.0.1" && u.User == nil
}
