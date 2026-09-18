package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// CA is a self-signed root that mints per-host leaf certificates on demand.
// cert.pem / key.pem are persisted so the CA survives restarts (install it
// into the system trust store once and it keeps working).
type CA struct {
	tlsCert  tls.Certificate
	template *x509.Certificate
	key      *rsa.PrivateKey

	mu    sync.Mutex
	cache map[string]*tls.Certificate
}

func LoadOrCreateCA(dir string) (*CA, error) {
	certPath := filepath.Join(dir, "cert.pem")
	keyPath := filepath.Join(dir, "key.pem")

	if _, err := os.Stat(certPath); err == nil {
		if _, err := os.Stat(keyPath); err == nil {
			return loadCA(certPath, keyPath)
		}
	}

	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, err
	}
	tpl := &x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: "stealth-kit TLS proxy CA", Organization: []string{"stealth-kit"}},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().AddDate(10, 0, 0),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
		BasicConstraintsValid: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &key.PublicKey, key)
	if err != nil {
		return nil, err
	}
	if err := writePEM(certPath, "CERTIFICATE", der, 0o644); err != nil {
		return nil, err
	}
	keyDER := x509.MarshalPKCS1PrivateKey(key)
	if err := writePEM(keyPath, "RSA PRIVATE KEY", keyDER, 0o600); err != nil {
		return nil, err
	}
	return buildCA(tpl, key)
}

func loadCA(certPath, keyPath string) (*CA, error) {
	tlsCert, err := tls.LoadX509KeyPair(certPath, keyPath)
	if err != nil {
		return nil, err
	}
	cert, err := x509.ParseCertificate(tlsCert.Certificate[0])
	if err != nil {
		return nil, err
	}
	key, ok := tlsCert.PrivateKey.(*rsa.PrivateKey)
	if !ok {
		return nil, fmt.Errorf("CA key is not RSA")
	}
	return &CA{tlsCert: tlsCert, template: cert, key: key, cache: map[string]*tls.Certificate{}}, nil
}

func buildCA(tpl *x509.Certificate, key *rsa.PrivateKey) (*CA, error) {
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &key.PublicKey, key)
	if err != nil {
		return nil, err
	}
	return &CA{
		tlsCert:  tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key},
		template: tpl,
		key:      key,
		cache:    map[string]*tls.Certificate{},
	}, nil
}

func writePEM(path, typ string, der []byte, perm os.FileMode) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, perm)
	if err != nil {
		return err
	}
	defer f.Close()
	return pem.Encode(f, &pem.Block{Type: typ, Bytes: der})
}

// CertFor returns a cached (or freshly minted) leaf certificate for host.
func (c *CA) CertFor(host string) (tls.Certificate, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if cert, ok := c.cache[host]; ok {
		return *cert, nil
	}

	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return tls.Certificate{}, err
	}
	leaf := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: host},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().AddDate(0, 0, 397),
		KeyUsage:     x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	if ip := net.ParseIP(host); ip != nil {
		leaf.IPAddresses = []net.IP{ip}
	} else {
		leaf.DNSNames = []string{host}
	}
	leafKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return tls.Certificate{}, err
	}
	der, err := x509.CreateCertificate(rand.Reader, leaf, c.template, &leafKey.PublicKey, c.key)
	if err != nil {
		return tls.Certificate{}, err
	}
	cert := tls.Certificate{Certificate: [][]byte{der, c.tlsCert.Certificate[0]}, PrivateKey: leafKey}
	c.cache[host] = &cert
	return cert, nil
}
