#!/bin/sh
set -eu

# A file bind mount can still reference the old inode after git replaces the
# file. Copy the checkout into the container and use a tested, writable include
# so existing Nginx containers can reload without being restarted.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
config=${AIPA_NGINX_CONFIG:-"$script_dir/../nginx/default.conf"}
docker compose exec -T nginx sh -c \
    'mkdir -p /tmp/aipriceaction-nginx && cat > /tmp/aipriceaction-nginx/candidate.conf' < "$config"

docker compose exec -T nginx sh -eu <<'NGINX_SCRIPT'
dir=/tmp/aipriceaction-nginx
main=/etc/nginx/nginx.conf
active=$dir/active.conf
candidate=$dir/candidate.conf
check=$dir/check-nginx.conf
backup_main=$dir/previous-nginx.conf
backup_active=$dir/previous-active.conf

# Accept the stock image or an earlier run of this script, and fail closed for
# any unexpected main configuration. Test the candidate before changing it.
sed -e 's@/etc/nginx/conf.d/\*.conf@/tmp/aipriceaction-nginx/candidate.conf@g' \
    -e 's@/tmp/aipriceaction-nginx/active.conf@/tmp/aipriceaction-nginx/candidate.conf@g' \
    "$main" > "$check"
grep -Fq '/tmp/aipriceaction-nginx/candidate.conf;' "$check"
nginx -t -c "$check"
cp "$main" "$backup_main"
had_active=false
if [ -f "$active" ]; then
    cp "$active" "$backup_active"
    had_active=true
fi

rollback() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then
        cp "$backup_main" "$main"
        if [ "$had_active" = true ]; then
            cp "$backup_active" "$active"
        else
            rm -f "$active"
        fi
        nginx -s reload >/dev/null 2>&1 || true
    fi
    exit "$result"
}
trap rollback EXIT
sed 's@/tmp/aipriceaction-nginx/candidate.conf@/tmp/aipriceaction-nginx/active.conf@g' \
    "$check" > "$dir/new-nginx.conf"
mv "$candidate" "$active"
cp "$dir/new-nginx.conf" "$main"
nginx -t
nginx -s reload
trap - EXIT
rm -f "$backup_main" "$backup_active" "$check" "$dir/new-nginx.conf"
NGINX_SCRIPT
