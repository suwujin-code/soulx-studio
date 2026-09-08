package main

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func zipBlob(names ...string) []byte {
	var b bytes.Buffer
	z := zip.NewWriter(&b)
	for _, n := range names {
		f, _ := z.Create(n)
		f.Write([]byte("hello"))
	}
	z.Close()
	return b.Bytes()
}
func testApp(t *testing.T) *App {
	t.Helper()
	a, e := newApp(t.TempDir(), 19781)
	if e != nil {
		t.Fatal(e)
	}
	a.address = "127.0.0.1:19779"
	t.Cleanup(func() { a.logfile.Close() })
	return a
}
func request(a *App, method, path, body, token, origin string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, "http://"+a.address+path, strings.NewReader(body))
	r.Host = a.address
	r.Header.Set("X-SoulX-Token", token)
	if origin != "" {
		r.Header.Set("Origin", origin)
	}
	w := httptest.NewRecorder()
	a.handler().ServeHTTP(w, r)
	return w
}
func TestDefaultRoots(t *testing.T) {
	for _, osname := range []string{"darwin", "windows", "linux"} {
		p, e := defaultRoot(osname, "/home/u", "/local", "/xdg")
		if e != nil || !strings.HasSuffix(p, "SoulXStudio") {
			t.Fatal(p, e)
		}
	}
	if _, e := defaultRoot("windows", "", "", ""); e == nil {
		t.Fatal("empty home accepted")
	}
}
func TestArchiveTraversal(t *testing.T) {
	for _, name := range []string{"../escape", "/absolute", "a/../../x", "C:/file", "a\\b", "a/./b", "a//b", "file.", "file "} {
		if e := extractPayload(zipBlob(name), t.TempDir()); e == nil {
			t.Fatalf("unsafe path accepted %q", name)
		}
	}
}
func TestArchiveDuplicateCase(t *testing.T) {
	if e := extractPayload(zipBlob("a.py", "A.py"), t.TempDir()); e == nil {
		t.Fatal("case collision accepted")
	}
}
func TestArchiveExtractAndNoOverwrite(t *testing.T) {
	d := t.TempDir()
	b := zipBlob("workbench/x.py")
	if e := extractPayload(b, d); e != nil {
		t.Fatal(e)
	}
	if e := extractPayload(b, d); e == nil {
		t.Fatal("overwrote existing files")
	}
}
func TestEmbeddedPayload(t *testing.T) {
	d := t.TempDir()
	if e := extractPayload(payload, d); e != nil {
		t.Fatal(e)
	}
	for _, p := range []string{"desktop_server.py", "run_studio.py", "workbench/process_lock.py", "frontend/dist/index.html", "packaging/OFFLINE_MODEL_MANIFEST.json"} {
		if _, e := os.Stat(filepath.Join(d, p)); e != nil {
			t.Fatal(e)
		}
	}
	if _, e := os.Stat(filepath.Join(d, "torchaudio")); e == nil {
		t.Fatal("shadowing shim was shipped")
	}
}
func TestPayloadPreservesWorkspace(t *testing.T) {
	a := testApp(t)
	d := filepath.Join(a.root, "workspace")
	os.MkdirAll(d, 0700)
	p := filepath.Join(d, "keep.txt")
	os.WriteFile(p, []byte("sound-assets"), 0600)
	first, e := a.source()
	if e != nil {
		t.Fatal(e)
	}
	second, e := a.source()
	if e != nil || second != first {
		t.Fatal("not idempotent", e)
	}
	b, _ := os.ReadFile(p)
	if string(b) != "sound-assets" {
		t.Fatal("workspace overwritten")
	}
}
func TestOfficialArtifacts(t *testing.T) {
	for _, pair := range [][2]string{{"darwin", "arm64"}, {"darwin", "amd64"}, {"windows", "amd64"}, {"linux", "amd64"}} {
		if _, e := uvArtifact(pair[0], pair[1]); e != nil {
			t.Fatal(e)
		}
	}
	if _, e := uvArtifact("windows", "386"); e == nil {
		t.Fatal("unsupported architecture accepted")
	}
}
func TestChecksum(t *testing.T) {
	good := strings.Repeat("a", 64)
	s, e := checksum([]byte(good + "  uv.zip\n"))
	if e != nil || s != good {
		t.Fatal(s, e)
	}
	for _, bad := range []string{"", strings.Repeat("z", 64), "short"} {
		if _, e := checksum([]byte(bad)); e == nil {
			t.Fatal("invalid checksum accepted")
		}
	}
}
func TestExtractRuntimeZip(t *testing.T) {
	b, e := executableFromArchive(zipBlob("uv-x86_64/uv.exe"), "uv.zip")
	if e != nil || string(b) != "hello" {
		t.Fatal(string(b), e)
	}
	if _, e := executableFromArchive(zipBlob("other.exe"), "uv.zip"); e == nil {
		t.Fatal("missing uv accepted")
	}
}
func TestExtractRuntimeTar(t *testing.T) {
	var b bytes.Buffer
	g := gzip.NewWriter(&b)
	tw := tar.NewWriter(g)
	tw.WriteHeader(&tar.Header{Name: "uv-arm/uv", Size: 5, Mode: 0755, Typeflag: tar.TypeReg})
	tw.Write([]byte("hello"))
	tw.Close()
	g.Close()
	out, e := executableFromArchive(b.Bytes(), "uv.tar.gz")
	if e != nil || string(out) != "hello" {
		t.Fatal(e)
	}
}
func TestHTTPDownloadLimits(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, "123456") }))
	defer s.Close()
	if _, e := fetch(context.Background(), s.Client(), s.URL, 3); e == nil {
		t.Fatal("limit ignored")
	}
	if b, e := fetch(context.Background(), s.Client(), s.URL, 8); e != nil || string(b) != "123456" {
		t.Fatal(e)
	}
}
func TestHTTPDownloadStatus(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) }))
	defer s.Close()
	if _, e := fetch(context.Background(), s.Client(), s.URL, 5); e == nil {
		t.Fatal("bad status accepted")
	}
}
func TestHTTPDownloadCancellation(t *testing.T) {
	ctx, c := context.WithCancel(context.Background())
	c()
	if _, e := fetch(ctx, http.DefaultClient, "http://127.0.0.1:1", 5); e == nil {
		t.Fatal("cancel ignored")
	}
}
func TestLocalActionsRequireToken(t *testing.T) {
	a := testApp(t)
	if w := request(a, "GET", "/api/state", "", "", ""); w.Code != 403 {
		t.Fatal(w.Code)
	}
	if w := request(a, "GET", "/api/state", "", a.token, ""); w.Code != 200 {
		t.Fatal(w.Code)
	}
}
func TestCrossOriginDenied(t *testing.T) {
	a := testApp(t)
	if w := request(a, "POST", "/api/install", "{}", a.token, "https://example.org"); w.Code != 403 {
		t.Fatal(w.Code)
	}
}
func TestHostRebindingDenied(t *testing.T) {
	a := testApp(t)
	r := httptest.NewRequest("GET", "http://evil.example/", nil)
	r.Host = "evil.example"
	w := httptest.NewRecorder()
	a.handler().ServeHTTP(w, r)
	if w.Code != 403 {
		t.Fatal(w.Code)
	}
}
func TestSetupHTMLAndMethods(t *testing.T) {
	a := testApp(t)
	w := request(a, "GET", "/", "", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), a.token) {
		t.Fatal("setup page unavailable")
	}
	if w = request(a, "GET", "/api/install", "", a.token, ""); w.Code != 405 {
		t.Fatal(w.Code)
	}
}
func TestProfileValidation(t *testing.T) {
	for _, x := range []struct {
		r    InstallRequest
		o, a string
	}{{InstallRequest{Profile: "desktop"}, "windows", "amd64"}, {InstallRequest{Profile: "x", Consent: true}, "windows", "amd64"}, {InstallRequest{Profile: "speech", Consent: true}, "darwin", "amd64"}, {InstallRequest{Profile: "speech-cuda", Consent: true}, "darwin", "arm64"}, {InstallRequest{Profile: "desktop", Consent: true, ModelDir: "relative"}, "windows", "amd64"}} {
		if e := validateRequest(x.r, x.o, x.a); e == nil {
			t.Fatal("bad request accepted", x)
		}
	}
	if e := validateRequest(InstallRequest{Profile: "speech", Consent: true}, "darwin", "arm64"); e != nil {
		t.Fatal(e)
	}
}
func TestInstallRejectsNoConsent(t *testing.T) {
	a := testApp(t)
	w := request(a, "POST", "/api/install", `{"profile":"desktop","consent":false}`, a.token, "")
	if w.Code != 409 || a.snapshot().Busy {
		t.Fatal("unexpected install")
	}
}
func TestUnknownFieldsRejected(t *testing.T) {
	a := testApp(t)
	w := request(a, "POST", "/api/install", `{"profile":"desktop","consent":true,"shell":"evil"}`, a.token, "")
	if w.Code != 409 {
		t.Fatal(w.Code)
	}
}
func TestStateDoesNotLeakTokens(t *testing.T) {
	a := testApp(t)
	s := request(a, "GET", "/api/state", "", a.token, "").Body.String()
	if strings.Contains(s, a.token) {
		t.Fatal("secret leaked in diagnostics")
	}
}
func TestConcurrentOperations(t *testing.T) {
	a := testApp(t)
	started := make(chan struct{})
	e := a.begin("test", func(ctx context.Context) error { close(started); <-ctx.Done(); return ctx.Err() })
	if e != nil {
		t.Fatal(e)
	}
	<-started
	if e = a.begin("duplicate", func(context.Context) error { return nil }); e == nil {
		t.Fatal("duplicate accepted")
	}
	request(a, "POST", "/api/cancel", "{}", a.token, "")
	for i := 0; i < 100 && a.snapshot().Busy; i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if a.snapshot().Busy {
		t.Fatal("cancel failed")
	}
}
func TestAtomicInstallRecord(t *testing.T) {
	p := filepath.Join(t.TempDir(), "installed.json")
	if e := writeJSON(p, Installed{Version: "old"}); e != nil {
		t.Fatal(e)
	}
	if e := writeJSON(p, Installed{Version: Version}); e != nil {
		t.Fatal(e)
	}
	b, _ := os.ReadFile(p)
	var x Installed
	if json.Unmarshal(b, &x) != nil || x.Version != Version {
		t.Fatal("invalid atomic record")
	}
}
func TestRejectExternalPythonRecord(t *testing.T) {
	a := testApp(t)
	writeJSON(filepath.Join(a.root, "installed.json"), Installed{Source: "/outside", Python: "/outside/python"})
	a.loadInstalled()
	if a.installed != nil {
		t.Fatal("unsafe record accepted")
	}
}
func TestInsideRoot(t *testing.T) {
	r := t.TempDir()
	if !inside(r, filepath.Join(r, "safe", "x")) || inside(r, filepath.Join(r, "..", "evil")) {
		t.Fatal("containment error")
	}
}
func TestLogBounded(t *testing.T) {
	a := testApp(t)
	for i := 0; i < 400; i++ {
		a.log("line")
	}
	if len(a.snapshot().Logs) != 180 {
		t.Fatal("unbounded logs")
	}
}
func TestLocalURL(t *testing.T) {
	for _, bad := range []string{"https://example.com", "file:///tmp/x", "http://127.0.0.1@evil.com", "http://localhost/"} {
		if validLocalURL(bad) {
			t.Fatal(bad)
		}
	}
	if !validLocalURL("http://127.0.0.1:123/app/") {
		t.Fatal("local URL rejected")
	}
}
func TestPlatformCommandCompiles(t *testing.T) {
	if runtime.GOOS == "windows" {
		if _, e := uvArtifact("windows", "amd64"); e != nil {
			t.Fatal(e)
		}
	}
}
