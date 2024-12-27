# VmGrid

VmGrid is a distributed virtual machine management system that enables efficient creation, deletion, and monitoring of virtual machines across different virtualization environments.

## Features

- Multi-hypervisor support (Docker, Proxmox, VirtualBox)
- Distributed architecture with high availability
- Resource monitoring and management
- GraphQL API for easy integration
- Task queuing system for reliable operations
- Atomic operations via ETCD

## Requirements

- Python 3.9+
- Docker
- Docker Compose
- etcd3 v3.5+

## Quick Start

1. Clone the repository:
```bash
git clone https://github.com/ryngach/vmgrid.git
cd vmgrid
```

2. Start the system using Docker Compose:
```bash
docker-compose up -d
```

This will start:
- 3 API server instances
- etcd cluster
- Required networking

## Configuration

Configuration is managed through YAML files in the `config` directory:

- `config/default.yaml` - Default configuration
- `config/development.yaml` - Development environment overrides
- `config/production.yaml` - Production environment overrides

Example configuration:
```yaml
server:
  host: "0.0.0.0"
  port: 5000
  workers: 4
  debug: true

etcd:
  host: "etcd"
  port: 2379
  timeout: 5
  retry_interval: 3
  max_retries: 5

graphql:
  path: "/graphql"
  graphiql: true
  batch: true
  max_depth: 15

logging:
  level: "INFO"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

hypervisor:
  type: "docker"
```

## API Usage

The system provides a GraphQL API for all operations. GraphiQL interface is available at `http://localhost:5000/graphql` in development mode.

### Create VMs mutation
```graphql
mutation {
  createVms(
    count: 2
    config: {
      cpu: 1
      ram: 512
      storage: 10
      name: "test-vm"
      image: "ubuntu:latest"
    }
    createdBy: "admin"
  ) {
    requestId
  }
}
```

### Delete VMs mutation
```graphql
mutation {
  deleteVms(
    vmIds: ["vm-uuid-1", "vm-uuid-1"]
  ) {
    success
  }
}
```

### Query VMs
```graphql
query {
  vms {
    id
    status
    config
    hypervisorId
    createdAt
  }
}
```

### Query Hypervisors
```graphql
{
  hypervisors {
    id
    type
    availableResources {
      cpu
      ram
      storage
    }
    vms
    createdAt
    updatedAt
  }
}
```

## Architecture

VmGrid uses a distributed architecture with the following components:

1. **API Server**:
   - GraphQL API interface
   - Task queue management
   - VM lifecycle handling

2. **Hypervisors**:
   - VM operations execution
   - Resource monitoring
   - Task queue processing

3. **ETCD**:
   - Distributed data storage
   - Atomic operations
   - State management

4. **Task Queues**:
   - Shared queue for general tasks
   - Hypervisor-specific queues


## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- [etcd](https://etcd.io/) for distributed storage
- [GraphQL](https://graphql.org/) for API interface
- [Docker](https://www.docker.com/) for containerization
- [Gevent](https://www.gevent.org/) for greenlet managemnet