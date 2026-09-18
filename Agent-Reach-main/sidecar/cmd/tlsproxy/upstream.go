package main

// Upstream egress proxy dialing (proxy chaining):
//
//	browser -> tlsproxy (JA3 rewrite) -> egress proxy (http|socks5) -> target
//
// so the target sees the egress IP together with the Chrome ClientHello.
// SOCKS5 is implemented by hand (RFC 1928/1929) to avoid a new dependency.

import (
	"bufio"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

// Upstream is a parsed egress proxy URL: http[s]:// or socks5://, with
// optional user:password authentication.
type Upstream struct {
	raw    string
	scheme string // "http", "https" or "socks5"
	addr   string // host:port of the egress proxy
	user   string
	pass   string
}

func parseUpstream(raw string) (*Upstream, error) {
	u, err := url.Parse(raw)
	if err != nil {
		return nil, fmt.Errorf("invalid upstream URL: %w", err)
	}
	scheme := u.Scheme
	if scheme == "socks5h" {
		scheme = "socks5"
	}
	switch scheme {
	case "http", "https", "socks5":
	default:
		return nil, fmt.Errorf("unsupported upstream scheme %q (want http:// or socks5://)", u.Scheme)
	}
	if u.Host == "" {
		return nil, fmt.Errorf("upstream URL has no host: %q", raw)
	}
	addr := u.Host
	if _, _, err := net.SplitHostPort(addr); err != nil {
		// Missing port: apply the scheme default.
		port := "1080"
		if scheme != "socks5" {
			port = "8080"
		}
		addr = net.JoinHostPort(u.Host, port)
	}
	up := &Upstream{raw: raw, scheme: scheme, addr: addr}
	if u.User != nil {
		up.user = u.User.Username()
		up.pass, _ = u.User.Password()
	}
	return up, nil
}

// dial reaches targetHost:targetPort through the egress proxy and returns a
// plain TCP tunnel ready for the uTLS handshake.
func (u *Upstream) dial(targetHost, targetPort string, timeout time.Duration) (net.Conn, error) {
	target := net.JoinHostPort(targetHost, targetPort)
	switch u.scheme {
	case "socks5":
		return u.dialSOCKS5(targetHost, targetPort, timeout)
	default:
		return u.dialHTTPConnect(target, timeout)
	}
}

// dialHTTPConnect tunnels through an HTTP proxy with a CONNECT request.
func (u *Upstream) dialHTTPConnect(target string, timeout time.Duration) (net.Conn, error) {
	conn, err := net.DialTimeout("tcp", u.addr, timeout)
	if err != nil {
		return nil, fmt.Errorf("upstream %s unreachable: %w", u.addr, err)
	}
	if err := conn.SetDeadline(time.Now().Add(timeout)); err != nil {
		conn.Close()
		return nil, err
	}

	header := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\n", target, target)
	if u.user != "" {
		token := base64.StdEncoding.EncodeToString([]byte(u.user + ":" + u.pass))
		header += "Proxy-Authorization: Basic " + token + "\r\n"
	}
	header += "\r\n"
	if _, err := io.WriteString(conn, header); err != nil {
		conn.Close()
		return nil, fmt.Errorf("upstream CONNECT write: %w", err)
	}

	br := bufio.NewReader(conn)
	resp, err := http.ReadResponse(br, &http.Request{Method: http.MethodConnect})
	if err != nil {
		conn.Close()
		return nil, fmt.Errorf("upstream CONNECT response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		resp.Body.Close()
		conn.Close()
		return nil, fmt.Errorf("upstream %s refused CONNECT %s: %s", u.addr, target, resp.Status)
	}
	// NOTE: no resp.Body.Close() — for a 200 CONNECT the body IS the tunnel
	// (same conn); closing it here would kill the tunnel we are returning.
	// Hand any bytes the proxy already sent past the 200 line to the caller.
	if err := conn.SetDeadline(time.Time{}); err != nil {
		conn.Close()
		return nil, err
	}
	return &bufferedConn{conn, br}, nil
}

// dialSOCKS5 performs a RFC 1928 CONNECT, with optional RFC 1929 auth.
func (u *Upstream) dialSOCKS5(host, port string, timeout time.Duration) (net.Conn, error) {
	conn, err := net.DialTimeout("tcp", u.addr, timeout)
	if err != nil {
		return nil, fmt.Errorf("upstream %s unreachable: %w", u.addr, err)
	}
	if err := conn.SetDeadline(time.Now().Add(timeout)); err != nil {
		conn.Close()
		return nil, err
	}
	fail := func(err error) (net.Conn, error) {
		conn.Close()
		return nil, err
	}

	// Greeting: offer no-auth, plus user/pass when credentials exist.
	methods := []byte{0x00}
	if u.user != "" {
		methods = append(methods, 0x02)
	}
	greeting := append([]byte{0x05, byte(len(methods))}, methods...)
	if _, err := conn.Write(greeting); err != nil {
		return fail(fmt.Errorf("socks5 greeting: %w", err))
	}
	reply := make([]byte, 2)
	if _, err := io.ReadFull(conn, reply); err != nil {
		return fail(fmt.Errorf("socks5 greeting reply: %w", err))
	}
	if reply[0] != 0x05 {
		return fail(fmt.Errorf("socks5: bad version %d in greeting reply", reply[0]))
	}
	switch reply[1] {
	case 0x00: // no auth
	case 0x02: // user/pass (RFC 1929)
		if u.user == "" {
			return fail(fmt.Errorf("socks5 %s requires authentication but no credentials given", u.addr))
		}
		if len(u.user) > 255 || len(u.pass) > 255 {
			return fail(fmt.Errorf("socks5: credentials too long"))
		}
		auth := []byte{0x01, byte(len(u.user))}
		auth = append(auth, u.user...)
		auth = append(auth, byte(len(u.pass)))
		auth = append(auth, u.pass...)
		if _, err := conn.Write(auth); err != nil {
			return fail(fmt.Errorf("socks5 auth write: %w", err))
		}
		if _, err := io.ReadFull(conn, reply); err != nil {
			return fail(fmt.Errorf("socks5 auth reply: %w", err))
		}
		if reply[1] != 0x00 {
			return fail(fmt.Errorf("socks5 %s: authentication failed", u.addr))
		}
	case 0xFF:
		return fail(fmt.Errorf("socks5 %s: no acceptable auth method", u.addr))
	default:
		return fail(fmt.Errorf("socks5: unsupported auth method 0x%02x", reply[1]))
	}

	// CONNECT request: prefer ATYP=domain (proxy resolves DNS), unless the
	// host is already an IP literal.
	req := []byte{0x05, 0x01, 0x00}
	if ip := net.ParseIP(host); ip != nil {
		if v4 := ip.To4(); v4 != nil {
			req = append(req, 0x01)
			req = append(req, v4...)
		} else {
			req = append(req, 0x04)
			req = append(req, ip.To16()...)
		}
	} else {
		if len(host) > 255 {
			return fail(fmt.Errorf("socks5: hostname too long"))
		}
		req = append(req, 0x03, byte(len(host)))
		req = append(req, host...)
	}
	portNum, err := strconv.Atoi(port)
	if err != nil {
		return fail(fmt.Errorf("socks5: bad port %q", port))
	}
	req = binary.BigEndian.AppendUint16(req, uint16(portNum))
	if _, err := conn.Write(req); err != nil {
		return fail(fmt.Errorf("socks5 CONNECT write: %w", err))
	}

	head := make([]byte, 4)
	if _, err := io.ReadFull(conn, head); err != nil {
		return fail(fmt.Errorf("socks5 CONNECT reply: %w", err))
	}
	if head[1] != 0x00 {
		return fail(fmt.Errorf("socks5 %s: CONNECT %s failed, code 0x%02x", u.addr, net.JoinHostPort(host, port), head[1]))
	}
	// Drain the bound address (ATYP-dependent) + port from the reply.
	var skip int
	switch head[3] {
	case 0x01:
		skip = 4
	case 0x04:
		skip = 16
	case 0x03:
		lenBuf := make([]byte, 1)
		if _, err := io.ReadFull(conn, lenBuf); err != nil {
			return fail(fmt.Errorf("socks5 reply addr: %w", err))
		}
		skip = int(lenBuf[0])
	default:
		return fail(fmt.Errorf("socks5: bad ATYP 0x%02x in reply", head[3]))
	}
	if _, err := io.ReadFull(conn, make([]byte, skip+2)); err != nil {
		return fail(fmt.Errorf("socks5 reply drain: %w", err))
	}

	if err := conn.SetDeadline(time.Time{}); err != nil {
		conn.Close()
		return nil, err
	}
	return conn, nil
}
