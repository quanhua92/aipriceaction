#!/bin/sh
set -eu

wait_healthy() {
    service=$1
    container=$2
    attempts=90

    while [ "$attempts" -gt 0 ]; do
        status=$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || true)
        if [ "$status" = healthy ]; then
            return 0
        fi
        if [ "$status" = unhealthy ]; then
            docker compose logs --no-color --tail=100 "$service" >&2
            echo "$container became unhealthy" >&2
            return 1
        fi
        attempts=$((attempts - 1))
        sleep 2
    done

    docker compose logs --no-color --tail=100 "$service" >&2
    echo "Timed out waiting for $container to become healthy" >&2
    return 1
}

# Keep port 8899 served while fetching the proxy image and rebuilding the API.
docker compose pull nginx
docker compose build api api2 worker worker-hourly worker-minute worker-history

deploy_replica() {
    service=$1
    container=$2
    docker compose up -d --no-deps "$service"
    wait_healthy "$service" "$container"
    echo "Warming and verifying homepage routes on $service"
    docker compose exec -T "$service" python -m aipriceaction_api.homepage_warmup --instant-ms 5000
}

if docker inspect aipriceaction-nginx >/dev/null 2>&1 \
    && [ "$(docker inspect --format '{{.State.Running}}' aipriceaction-nginx)" = true ]; then
    # Nginx stays live while each backend is replaced and refreshed in DNS.
    deploy_replica api2 aipriceaction-api-2
    docker compose exec -T nginx nginx -s reload
    deploy_replica api aipriceaction-api
    docker compose exec -T nginx nginx -s reload
else
    # First cutover: keep the old API on host port 8899 while api2 warms, then
    # start Nginx as soon as the old api container has been replaced. Api2
    # already serves requests during api's startup and warmup.
    deploy_replica api2 aipriceaction-api-2
    docker compose up -d --no-deps api
    wait_healthy api aipriceaction-api
    echo "Warming and verifying homepage routes on api"
    docker compose exec -T api python -m aipriceaction_api.homepage_warmup --instant-ms 5000
    docker compose up -d --no-deps nginx
    wait_healthy nginx aipriceaction-nginx
fi

docker compose up -d --no-deps worker worker-hourly worker-minute worker-history
echo "Checking the exact 19 homepage routes through Nginx"
docker compose exec -T api python -m aipriceaction_api.homepage_warmup --base-url http://nginx:8899
docker compose ps
