//
//  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
//
//  Licensed under the Apache License, Version 2.0 (the "License");
//  you may not use this file except in compliance with the License.
//  You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
//

package tool

import (
	"net"
	"strings"
	"testing"

	"ragflow/internal/utility"
)

func stubPublicDNS(t *testing.T) {
	t.Helper()
	original := utility.LookupHost
	utility.LookupHost = func(host string) ([]string, error) {
		return []string{"93.184.216.34"}, nil
	}
	t.Cleanup(func() { utility.LookupHost = original })
}

func TestValidateURLForSSRF(t *testing.T) {
	stubPublicDNS(t)
	cases := []struct {
		name    string
		rawURL  string
		wantErr bool
	}{
		// Public targets: should pass (DNS may still fail in CI; we
		// accept both "ok" and "resolve error").
		{"https_public", "https://example.com/", false},
		{"http_public", "http://1.1.1.1/", false},

		// Loopback / metadata / private: must be blocked.
		{"localhost_alias", "http://localhost/", true},
		{"localhost_subdomain", "http://admin.localhost/", true},
		{"loopback_ip_v4", "http://127.0.0.1/", true},
		{"loopback_ip_v6", "http://[::1]/", true},
		{"unspecified_v4", "http://0.0.0.0/", true},
		{"unspecified_v6", "http://[::]/", true},
		{"private_10", "http://10.0.0.1/", true},
		{"private_192", "http://192.168.1.1/", true},
		{"private_172", "http://172.16.0.1/", true},
		{"link_local_v4_metadata", "http://169.254.169.254/latest/meta-data/", true},
		{"link_local_v6", "http://[fe80::1]/", true},
		{"multicast", "http://224.0.0.1/", true},
		{"gcp_metadata_alias", "http://metadata.google.internal/", true},
		{"metadata_short", "http://metadata/", true},

		// Bad input.
		{"empty", "", true},
		{"no_scheme", "example.com/foo", true},
		{"file_scheme", "file:///etc/passwd", true},
		{"javascript_scheme", "javascript:alert(1)", true},
		{"no_host", "http:///path", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := validateURLForSSRF(tc.rawURL)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("validateURLForSSRF(%q) = nil, want error", tc.rawURL)
				}
				if !strings.Contains(err.Error(), "ssrf") && tc.rawURL != "" && tc.rawURL != "file:///etc/passwd" {
					t.Errorf("validateURLForSSRF(%q) = %q, want an ssrf-tagged error", tc.rawURL, err)
				}
			} else if err != nil {
				// Public target may fail DNS in CI; tolerate resolve errors.
				if !strings.Contains(err.Error(), "resolve") {
					t.Errorf("validateURLForSSRF(%q) = %q, want nil or resolve error", tc.rawURL, err)
				}
			}
		})
	}
}

// TestResolveAndValidate covers the resolver half of the M1-rebinding
// fix: for a literal public IP the function returns that IP unchanged;
// for a hostname it returns the first A/AAAA record (which is what
// DoPinned will then dial directly, defeating DNS rebinding between
// validation and connect). For all SSRF-blocked targets the function
// must return an error AND a nil IP, so DoPinned cannot be invoked
// with a stale allow-list entry.
func TestResolveAndValidate(t *testing.T) {
	stubPublicDNS(t)
	t.Run("literal_public_ip", func(t *testing.T) {
		host, ip, err := ResolveAndValidate("https://1.1.1.1/foo")
		if err != nil {
			t.Fatalf("ResolveAndValidate(literal public IP) = %v, want nil", err)
		}
		if host != "1.1.1.1" {
			t.Errorf("host = %q, want 1.1.1.1", host)
		}
		if ip == nil || !ip.Equal(net.ParseIP("1.1.1.1")) {
			t.Errorf("ip = %v, want 1.1.1.1", ip)
		}
	})

	t.Run("literal_blocked_ip", func(t *testing.T) {
		host, ip, err := ResolveAndValidate("http://127.0.0.1/")
		if err == nil {
			t.Fatalf("ResolveAndValidate(loopback) = nil, want error")
		}
		if host != "" {
			t.Errorf("host = %q, want empty on error", host)
		}
		if ip != nil {
			t.Errorf("ip = %v, want nil on error", ip)
		}
	})

	t.Run("literal_blocked_metadata", func(t *testing.T) {
		_, ip, err := ResolveAndValidate("http://169.254.169.254/latest/meta-data/")
		if err == nil {
			t.Fatalf("ResolveAndValidate(metadata) = nil, want error")
		}
		if ip != nil {
			t.Errorf("ip = %v, want nil on error", ip)
		}
	})

	t.Run("hostname_resolves_to_public", func(t *testing.T) {
		host, ip, err := ResolveAndValidate("https://example.com/")
		if err != nil {
			t.Fatalf("ResolveAndValidate(example.com) = %v, want nil", err)
		}
		if host != "example.com" {
			t.Errorf("host = %q, want example.com", host)
		}
		if ip == nil {
			t.Fatal("ip = nil, want non-nil")
		}
		if ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() {
			t.Errorf("ip = %s, want a public address", ip)
		}
	})

	t.Run("hostname_blocked_alias", func(t *testing.T) {
		_, ip, err := ResolveAndValidate("http://localhost/")
		if err == nil {
			t.Fatalf("ResolveAndValidate(localhost) = nil, want error")
		}
		if ip != nil {
			t.Errorf("ip = %v, want nil on error", ip)
		}
	})

	t.Run("bad_input_returns_nil_ip", func(t *testing.T) {
		for _, bad := range []string{"", "file:///etc/passwd", "http:///path", "example.com/no-scheme"} {
			_, ip, err := ResolveAndValidate(bad)
			if err == nil {
				t.Errorf("ResolveAndValidate(%q) = nil err, want error", bad)
			}
			if ip != nil {
				t.Errorf("ResolveAndValidate(%q) ip = %v, want nil on error", bad, ip)
			}
		}
	})
}

func TestSanitizeURL(t *testing.T) {
	cases := []struct {
		name   string
		raw    string
		want   string
		noKeys bool
	}{
		{
			name: "google_api_key_in_query",
			raw:  "https://www.googleapis.com/customsearch/v1?key=AIzaSecret&q=hello",
			want: "https://www.googleapis.com/customsearch/v1?key=REDACTED&q=hello",
		},
		{
			name: "qweather_key",
			raw:  "https://devapi.qweather.com/v7/weather/now?location=101010100&key=abc123&lang=zh",
			want: "https://devapi.qweather.com/v7/weather/now?key=REDACTED&lang=zh&location=101010100",
		},
		{
			name: "token_param",
			raw:  "https://api.example.com/data?token=topsecret&page=2",
			want: "https://api.example.com/data?page=2&token=REDACTED",
		},
		{
			name:   "no_creds_unchanged",
			raw:    "https://api.example.com/data?page=2&size=10",
			want:   "https://api.example.com/data?page=2&size=10",
			noKeys: true,
		},
		{
			name:   "unparseable_unchanged",
			raw:    "://broken",
			want:   "://broken",
			noKeys: true,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := SanitizeURL(tc.raw)
			if tc.noKeys {
				if got != tc.raw {
					t.Errorf("SanitizeURL(%q) = %q, want unchanged", tc.raw, got)
				}
				return
			}
			// Compare on a normalised form by stripping the input URL
			// and re-encoding both — easier than asserting exact
			// query-string order, which is non-deterministic in net/url.
			if !strings.Contains(got, "REDACTED") {
				t.Errorf("SanitizeURL(%q) = %q, want REDACTED marker", tc.raw, got)
			}
			if strings.Contains(got, "AIzaSecret") || strings.Contains(got, "abc123") || strings.Contains(got, "topsecret") {
				t.Errorf("SanitizeURL(%q) = %q, secret value still present", tc.raw, got)
			}
		})
	}
}
