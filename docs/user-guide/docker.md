# Docker and Container Installs

Container proof is separate from local host proof. A clean container should install AEGIS, run the CLI, perform reference-doc checks, and execute the local test gate without reading host credentials.

Suggested live proof:

```bash
AEGIS_LIVE_DOCKER=1 bash tests/live/test_docker_install.sh
```
