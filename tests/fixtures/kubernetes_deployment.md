# Kubernetes Deployment - 滚动更新

## Deploying a Rolling Update

A rolling update allows you to update your application with zero downtime.
Kubernetes achieves this by incrementally replacing old Pods with new ones,
ensuring the Service always points to healthy instances.

### Key Concepts

1. **Deployment**: Controls the rollout of new ReplicaSets. You can pause,
   resume, or rollback a deployment at any time.
2. **Pod**: The smallest deployable unit in Kubernetes. Each Pod encapsulates
   one or more containers that share networking and storage.
3. **Readiness Probe**: Kubernetes uses readiness probes to know when a
   container is ready to start accepting traffic. Without it, traffic may
   be sent to unready Pods.

### Configuration Example

```yaml
apiVersion: apps/v1
kind: Deployment
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1
      maxSurge: 1
```

This configuration ensures at most one Pod can be unavailable during
the update, while one extra Pod can be created temporarily.

### Commands

```bash
$ kubectl apply -f deployment.yaml
$ kubectl rollout status deployment/nginx-deployment
$ kubectl get pods -l app=nginx
```

### Version Requirements

The deployment requires Kubernetes v1.19.0 or later. For earlier versions
(v1.16.x-v1.18.x), use `extensions/v1beta1` API version.

### Best Practices

- Always define a readiness probe to prevent traffic from reaching
  uninitialized containers.
- Use `kubectl rollout status` to monitor the progress of a deployment.
- Set `minReadySeconds` to allow the Pod to stabilize before marking
  the rollout as successful.
- For stateful workloads, consider using StatefulSets instead of Deployments.
