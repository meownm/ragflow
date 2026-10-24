---
sidebar_position: 1
slug: /config_ssl_cert
sidebar_custom_props: {
  categoryIcon: LucideCog
}
---
# Configure SSL certificates

Configure SSL certificates for a RAGFlow instance deployed via Docker.

---

This guide details how to configure SSL certificates for a RAGFlow instance deployed via Docker, using the container name `docker-ragflow-cpu-1` as an example.

## 1. Prepare certificate files

Ensure you have Nginx-formatted certificate files ready:

- **Public Key**: Usually named `fullchain.pem` or `server.crt`.
- **Private Key**: Usually named `privkey.pem` or `server.key`.

If necessary, rename your files to match the standard:

```bash
# Rename bundle to fullchain.pem
cp XXXXX_bundle.pem fullchain.pem
# Rename private key to privkey.pem
cp XXXXX.key privkey.pem
```

## 2. Confirm container status

Verify that your container is running:

```bash
docker ps
```

## 3. Copy certificates to the container

Transfer the files from your host machine to the container's temporary directory:

```bash
docker cp ./fullchain.pem docker-ragflow-cpu-1:/tmp/fullchain.pem
docker cp ./privkey.pem docker-ragflow-cpu-1:/tmp/privkey.pem
```

## 4. Deploy certificates inside the container

Enter the container's interactive terminal:

```bash
docker exec -it docker-ragflow-cpu-1 /bin/bash
```

Once inside, move the files and set appropriate permissions:

```bash
mkdir -p /etc/nginx/ssl
mv /tmp/fullchain.pem /etc/nginx/ssl/
mv /tmp/privkey.pem /etc/nginx/ssl/

# Set permissions: 644 for public key, 600 for private key
chmod 644 /etc/nginx/ssl/fullchain.pem
chmod 600 /etc/nginx/ssl/privkey.pem
```

## 5. Persist the HTTPS configuration

For a persistent setup, exit the container and edit `docker/nginx/ragflow.https.conf`
on the host. Replace `your-ragflow-domain.com` with your domain. The template uses
the Python API profile and includes ASR, translator and PlantUML routes;
Go/hybrid deployments require a matching custom configuration.

Mount the configuration and certificates read-only in the application service
(paths below are relative to the Compose file in `docker/`):

```yaml
services:
  ragflow-cpu:
    volumes:
      - ./nginx/ragflow.https.conf:/etc/nginx/conf.d/ragflow.conf:ro
      - ./ssl/fullchain.pem:/etc/nginx/ssl/fullchain.pem:ro
      - ./ssl/privkey.pem:/etc/nginx/ssl/privkey.pem:ro
```

Use your actual certificate paths and service name. An explicitly mounted
`ragflow.conf` takes precedence over automatic profile selection and is never
overwritten at startup, including when the mount is read-only.

This recipe is for a directly published application container. The local
blue/green gateway uses its own routing configuration; terminate TLS at an
external reverse proxy for that topology.

## 6. Validate and apply

Validate Compose before recreating the selected application service through
your deployment procedure. After recreation, validate Nginx inside the container:

```bash
docker compose config --quiet
docker exec docker-ragflow-cpu-1 nginx -t
```

For later edits to the mounted configuration, validate before a graceful reload:

```bash
docker exec docker-ragflow-cpu-1 nginx -t && docker exec docker-ragflow-cpu-1 nginx -s reload
```

## Configuration persistence

:::tip IMPORTANT
Files copied into a container are lost when it is removed. Keep the configuration
and certificates on the host and use the read-only mounts above. Restarting alone
does not install a new mount definition.
:::
