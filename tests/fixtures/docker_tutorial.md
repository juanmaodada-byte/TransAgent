# Docker Container Quick Start

## Getting Started

Docker provides a lightweight way to package and run applications in
isolated containers. This tutorial covers the essential commands for
daily development.

### Installation

For macOS, download Docker Desktop from `https://www.docker.com/products/docker-desktop`.
For Linux, use your package manager:

```bash
$ curl -fsSL https://get.docker.com -o get-docker.sh
$ sudo sh get-docker.sh
$ sudo systemctl enable docker
```

### Basic Commands

| Command | Description |
|---------|-------------|
| `docker run` | Create and start a container |
| `docker build` | Build an image from a Dockerfile |
| `docker ps` | List running containers |
| `docker stop` | Stop a running container |
| `docker rm` | Remove a container |

### Running Your First Container

```bash
$ docker run -d -p 8080:80 --name my-nginx nginx:1.25
$ curl http://localhost:8080
```

The `-d` flag runs the container in detached mode. `-p 8080:80` maps
host port 8080 to container port 80.

### Building an Image

Create a `Dockerfile` in your project directory:

```dockerfile
FROM node:18-alpine
WORKDIR /usr/src/app
COPY package*.json ./
RUN npm install
COPY . .
EXPOSE 3000
CMD ["node", "server.js"]
```

Build and run:

```bash
$ docker build -t my-app:v1.0.0 .
$ docker run -p 3000:3000 my-app:v1.0.0
```

### Docker Compose

For multi-container apps, use `docker-compose`:

```yaml
version: '3.8'
services:
  web:
    build: .
    ports:
      - "3000:3000"
  redis:
    image: redis:7-alpine
```

### Useful Links

- Docker docs: `https://docs.docker.com`
- Docker Hub: `https://hub.docker.com`
