#!/usr/bin/env bash
set -uo pipefail

# General server security check script
# Usage: bash scripts/helpers/check-server.sh [HOST]
# Examples:
#   bash scripts/helpers/check-server.sh 202.91.32.93
#   bash scripts/helpers/check-server.sh api.aipriceaction.com

HOST="${1:?Usage: check-server.sh <HOST_IP_OR_DOMAIN>}"
FAILED=0
PASSED=0
WARN=0

pass() { PASSED=$((PASSED + 1)); echo "  ✅ $1"; }
fail() { FAILED=$((FAILED + 1)); echo "  ❌ $1"; }
warn() { WARN=$((WARN + 1)); echo "  ⚠️  $1"; }

echo "=========================================="
echo " Security Check: $HOST"
echo " $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=========================================="

# ── Port Scan ──
echo ""
echo "── Open Ports ──"
COMMON_PORTS="22 80 443 3000 5432 6379 8080 8443 9090 27017 3306 6443 10250 2375 2376 15672 9200"
DB_PORTS="5432 6379 27017 3306 6379 11211 5984"
OPEN_PORTS=""

for port in $COMMON_PORTS; do
    if nc -z -w 2 "$HOST" "$port" 2>/dev/null; then
        OPEN_PORTS="$OPEN_PORTS $port"
        # Check if it's a database port
        case "$port" in
            5432|3306|27017|11211|5984|6379)
                fail "Database port $port is OPEN to the internet!"
                ;;
            2375|2376)
                fail "Docker API port $port is OPEN to the internet!"
                ;;
            10250|6443|9200)
                warn "K8s/Infra port $port is open (check if intentional)"
                ;;
            *)
                echo "  Port $port OPEN"
                ;;
        esac
    fi
done

if [ -z "$OPEN_PORTS" ]; then
    pass "No common service ports exposed"
fi

# ── Determine protocol ──
echo ""
echo "── Web Server ──"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 8 "http://$HOST" 2>/dev/null || echo "000")
HTTPS_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 8 -k "https://$HOST" 2>/dev/null || echo "000")
SERVER_HEADER=$(curl -s -I --connect-timeout 5 --max-time 8 "http://$HOST" 2>/dev/null | grep -i "^server:" | tr -d '\r' || true)

if [ "$HTTPS_CODE" != "000" ]; then
    PROTO="https"
    pass "HTTPS reachable ($HTTPS_CODE)"
else
    PROTO="http"
    warn "HTTPS not reachable, using HTTP"
fi

if [ "$HTTP_CODE" != "000" ] && [ "$HTTPS_CODE" != "000" ]; then
    warn "HTTP (port 80) open ($HTTP_CODE) — consider redirect to HTTPS only"
elif [ "$HTTP_CODE" != "000" ]; then
    warn "HTTP (port 80) open ($HTTP_CODE) — no HTTPS available"
else
    pass "HTTP not open"
fi

if [ -n "$SERVER_HEADER" ]; then
    echo "  Server: $SERVER_HEADER"
fi

# ── SSL Certificate ──
echo ""
echo "── SSL/TLS ──"
CERT_OUT=$(timeout 8 openssl s_client -connect "$HOST:443" -servername "$HOST" </dev/null 2>&1 || true)
CERT_SUBJECT=$(echo "$CERT_OUT" | grep "subject=" | head -1 || true)
CERT_ISSUER=$(echo "$CERT_OUT" | grep "issuer=" | head -1 || true)
CERT_ERROR=$(echo "$CERT_OUT" | grep -i "error\|errno\|verification" | head -1 || true)

if [ -n "$CERT_ERROR" ]; then
    fail "SSL error: $CERT_ERROR"
elif [ -n "$CERT_ISSUER" ]; then
    ISSUER=$(echo "$CERT_ISSUER" | sed 's/.*issuer=//')
    # Check for self-signed or default certs
    case "$ISSUER" in
        *"TRAEFIK"*|*"default"*|*"self"*|*"Self"*)
            fail "Self-signed/default cert: $ISSUER"
            ;;
        *"Let's Encrypt"*)
            pass "SSL valid (Let's Encrypt)"
            ;;
        *)
            pass "SSL cert issuer: $ISSUER"
            ;;
    esac
    [ -n "$CERT_SUBJECT" ] && echo "  Subject: $CERT_SUBJECT"
else
    warn "Cannot check SSL certificate (no HTTPS?)"
fi

# ── Security Headers ──
echo ""
echo "── Security Headers ──"
HEADERS=$(curl -s -I --connect-timeout 5 --max-time 8 -k "$PROTO://$HOST" 2>/dev/null || true)
if [ -z "$HEADERS" ]; then
    warn "Could not retrieve headers (timeout)"
else
    HEADER_XFO=$(echo "$HEADERS" | grep -i "x-frame-options" | tr -d '\r')
    HEADER_XCT=$(echo "$HEADERS" | grep -i "x-content-type-options" | tr -d '\r')
    HEADER_XXP=$(echo "$HEADERS" | grep -i "x-xss-protection" | tr -d '\r')
    HEADER_CSP=$(echo "$HEADERS" | grep -i "content-security-policy" | tr -d '\r')
    HEADER_STS=$(echo "$HEADERS" | grep -i "strict-transport-security" | tr -d '\r')

    [ -n "$HEADER_XFO" ] && pass "X-Frame-Options set" || fail "X-Frame-Options missing"
    [ -n "$HEADER_XCT" ] && pass "X-Content-Type-Options set" || fail "X-Content-Type-Options missing"
    [ -n "$HEADER_XXP" ] && pass "X-XSS-Protection set" || warn "X-XSS-Protection missing (modern browsers ignore this)"
    [ -n "$HEADER_CSP" ] && pass "Content-Security-Policy set" || warn "Content-Security-Policy missing"
    [ -n "$HEADER_STS" ] && pass "Strict-Transport-Security set" || fail "Strict-Transport-Security missing"
fi

# ── Sensitive File Exposure ──
echo ""
echo "── Sensitive File Exposure ──"
for path in "/.env" "/.git/HEAD" "/.git/config" "/.htaccess" "/.DS_Store" "/wp-admin" "/phpmyadmin" "/server-status" "/nginx_status" "/actuator" "/actuator/health" "/debug" "/console" "/.well-known/security.txt" "/robots.txt"; do
    CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 --max-time 5 -k "$PROTO://$HOST$path" 2>/dev/null || echo "000")
    if [ "$CODE" = "200" ]; then
        fail "$path → 200 (EXPOSED!)"
    elif [ "$CODE" = "404" ] || [ "$CODE" = "403" ] || [ "$CODE" = "000" ]; then
        pass "$path → not exposed ($CODE)"
    else
        warn "$path → $CODE"
    fi
done

# ── CORS Check ──
echo ""
echo "── CORS Configuration ──"
CORS_HEADER=$(curl -s -I --connect-timeout 5 --max-time 8 -k -H "Origin: https://evil-attacker.com" "$PROTO://$HOST/" 2>/dev/null | grep -i "access-control-allow-origin" | tr -d '\r' || true)
if [ -z "$CORS_HEADER" ]; then
    pass "CORS: no allow-origin for attacker domain"
elif echo "$CORS_HEADER" | grep -q "\*"; then
    fail "CORS: allows ALL origins (wildcard)"
else
    warn "CORS: allows unexpected origin: $CORS_HEADER"
fi

# ── SSH Check ──
echo ""
echo "── SSH ──"
if nc -z -w 2 "$HOST" 22 2>/dev/null; then
    SSH_BANNER=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes "$HOST" 2>&1 | head -1 || true)
    SSH_AUTH=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes -o PreferredAuthentications=password root@"$HOST" 2>&1 | grep -oE 'publickey|password|keyboard-interactive' || true)

    [ -n "$SSH_BANNER" ] && echo "  Banner: $SSH_BANNER"

    if echo "$SSH_AUTH" | grep -q "password"; then
        fail "SSH: password authentication allowed!"
    elif echo "$SSH_AUTH" | grep -q "publickey"; then
        pass "SSH: key-only auth (password disabled)"
    else
        warn "SSH: open but could not determine auth methods"
    fi
else
    pass "SSH not exposed"
fi

# ── Summary ──
echo ""
echo "=========================================="
TOTAL=$((PASSED + FAILED + WARN))
echo " Results: $PASSED passed, $FAILED failed, $WARN warnings (of $TOTAL checks)"
echo "=========================================="

[ "$FAILED" -eq 0 ]
