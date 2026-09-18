package main

import (
	"bufio"
	"crypto/tls"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"time"

	utls "github.com/refraction-networking/utls"
)

// ProxyServer accepts HTTP CONNECT (and plain-HTTP absolute-URI) requests.
type ProxyServer struct {
	CA          *CA
	IdleTimeout time.Duration
	Upstream    *Upstream // nil = direct egress
}

// dialTarget reaches host:port either directly or through the egress proxy.
func (s *ProxyServer) dialTarget(host, port string) (net.Conn, error) {
	if s.Upstream != nil {
		return s.Upstream.dial(host, port, 15*time.Second)
	}
	return net.DialTimeout("tcp", net.JoinHostPort(host, port), 15*time.Second)
}

func (s *ProxyServer) Serve(ln net.Listener) error {
	for {
		conn, err := ln.Accept()
		if err != nil {
			if ne, ok := err.(net.Error); ok && ne.Temporary() {
				continue
			}
			return err
		}
		go s.handle(conn)
	}
}

// bufferedConn replays bytes the request parser already buffered before
// falling through to the raw connection (the TLS ClientHello can arrive in
// the same TCP segment as the CONNECT request).
type bufferedConn struct {
	net.Conn
	r *bufio.Reader
}

func (c *bufferedConn) Read(p []byte) (int, error) { return c.r.Read(p) }

func (s *ProxyServer) handle(conn net.Conn) {
	defer conn.Close()
	defer func() {
		if r := recover(); r != nil {
			log.Printf("connection panic (recovered): %v", r)
		}
	}()
	br := bufio.NewReader(conn)
	for {
		req, err := http.ReadRequest(br)
		if err != nil {
			return // client went away or sent garbage — nothing to salvage
		}
		if req.Method == http.MethodConnect {
			s.handleConnect(&bufferedConn{conn, br}, req)
			return // tunnel owns the connection from here
		}
		if err := s.handlePlainHTTP(conn, req); err != nil {
			return
		}
	}
}

// handlePlainHTTP forwards plain http:// requests directly (no TLS involved).
func (s *ProxyServer) handlePlainHTTP(conn net.Conn, req *http.Request) error {
	req.RequestURI = ""
	req.Header.Del("Proxy-Connection")
	resp, err := http.DefaultTransport.RoundTrip(req)
	if err != nil {
		fmt.Fprintf(conn, "HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
		return err
	}
	defer resp.Body.Close()
	if err := resp.Write(conn); err != nil {
		return err
	}
	if req.Close || resp.Close {
		return io.EOF
	}
	return nil
}

func (s *ProxyServer) handleConnect(conn net.Conn, req *http.Request) {
	host, port, err := net.SplitHostPort(req.Host)
	if err != nil {
		host = req.Host
		port = "443"
	}

	upstream, err := s.dialTarget(host, port)
	if err != nil {
		fmt.Fprintf(conn, "HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
		log.Printf("dial %s: %v", req.Host, err)
		return
	}

	if _, err := fmt.Fprintf(conn, "HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
		upstream.Close()
		return
	}

	// Plain HTTP over CONNECT (port 80): raw tunnel, no TLS anywhere.
	if port == "80" {
		relay(conn, upstream, s.IdleTimeout)
		return
	}

	// Browser side: terminate TLS with a minted site certificate.
	leaf, err := s.CA.CertFor(host)
	if err != nil {
		upstream.Close()
		log.Printf("cert for %s: %v", host, err)
		return
	}
	clientTLS := tls.Server(conn, &tls.Config{
		Certificates: []tls.Certificate{leaf},
		NextProtos:   []string{"h2", "http/1.1"},
	})
	if err := clientTLS.Handshake(); err != nil {
		upstream.Close()
		return // e.g. client rejected our CA — logged at debug level only
	}

	// Upstream side: re-handshake with a genuine Chrome ClientHello so the
	// peer sees a Chrome JA3/JA4. ALPN follows what the browser negotiated
	// with us, so both legs speak the same application protocol.
	negotiated := clientTLS.ConnectionState().NegotiatedProtocol
	nextProtos := []string{"h2", "http/1.1"}
	if negotiated != "" {
		nextProtos = []string{negotiated}
	}
	upstreamTLS := utls.UClient(upstream, &utls.Config{
		ServerName: host,
		NextProtos: nextProtos,
	}, utls.HelloChrome_Auto)
	if err := upstreamTLS.Handshake(); err != nil {
		clientTLS.Close()
		log.Printf("utls handshake %s: %v", host, err)
		return
	}

	// Both legs are TLS-terminated plaintext now — relay bytes blindly;
	// works for h2 and http/1.1 alike.
	relay(clientTLS, upstreamTLS, s.IdleTimeout)
}

// relay copies bidirectionally and returns when either direction ends;
// both connections are closed on return.
func relay(a, b net.Conn, idle time.Duration) {
	done := make(chan struct{}, 2)
	go func() { copyIdle(a, b, idle); done <- struct{}{} }()
	go func() { copyIdle(b, a, idle); done <- struct{}{} }()
	<-done
	a.Close()
	b.Close()
	// Drain the second goroutine so it can exit once its peer is closed.
	<-done
}

func copyIdle(dst, src net.Conn, idle time.Duration) {
	buf := make([]byte, 32*1024)
	for {
		if idle > 0 {
			_ = src.SetReadDeadline(time.Now().Add(idle))
		}
		n, err := src.Read(buf)
		if n > 0 {
			if idle > 0 {
				_ = dst.SetWriteDeadline(time.Now().Add(idle))
			}
			if _, werr := dst.Write(buf[:n]); werr != nil {
				return
			}
		}
		if err != nil {
			return
		}
	}
}
