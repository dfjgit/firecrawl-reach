// tlsproxy is a local MITM forward proxy: the browser speaks TLS to us with
// a site certificate we mint on the fly; we re-originate the upstream TLS
// handshake with uTLS using a genuine Chrome ClientHello, so the peer sees a
// Chrome JA3/JA4 instead of the automation browser's real one.
//
// Usage: tlsproxy -addr 127.0.0.1:0 -cadir ca
// The first stdout line is the actual listen address (for the parent
// process to parse the chosen port).
package main

import (
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"time"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:0", "listen address")
	caDir := flag.String("cadir", "ca", "directory holding the MITM CA (cert.pem / key.pem)")
	idle := flag.Duration("idle", 120*time.Second, "idle timeout per relayed connection")
	upstreamRaw := flag.String("upstream", "", "optional egress proxy: http://user:pass@host:port or socks5://user:pass@host:port")
	flag.Parse()

	ca, err := LoadOrCreateCA(*caDir)
	if err != nil {
		log.Fatalf("ca: %v", err)
	}

	var upstream *Upstream
	if *upstreamRaw != "" {
		upstream, err = parseUpstream(*upstreamRaw)
		if err != nil {
			log.Fatalf("upstream: %v", err)
		}
		log.Printf("egress via upstream proxy %s://%s", upstream.scheme, upstream.addr)
	}

	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("listen: %v", err)
	}
	// First stdout line: the address the parent process should proxy to.
	fmt.Printf("%s\n", ln.Addr().String())
	_ = os.Stdout.Sync()

	srv := &ProxyServer{CA: ca, IdleTimeout: *idle, Upstream: upstream}
	log.Printf("tlsproxy serving on %s", ln.Addr())
	if err := srv.Serve(ln); err != nil {
		log.Fatalf("serve: %v", err)
	}
}
