---
name: kubernetes
description: Write and apply Kubernetes manifests (Deployment/Service/Ingress/ConfigMap) and debug pods with kubectl. Use for k8s deploy/ops tasks.
version: 1.0.0
metadata:
  category: devops
  tags: [kubernetes, kubectl, deployment, manifests]
requires:
  bins: [kubectl]
---

## When to Use
Deploying apps to a Kubernetes cluster, authoring/editing manifests, or debugging
failing pods, services, or networking. Skip for raw Docker-only tasks.

## Procedure
1. Verify cluster access: `kubectl cluster-info` and `kubectl config current-context`.
   Confirm the right context BEFORE applying anything.
2. Pick/confirm the target namespace (`kubectl get ns`). Default to an explicit
   `-n <ns>` rather than relying on the active context's default.
3. Author manifests with write_file. One concern per file (deployment.yaml,
   service.yaml, etc.) or a single multi-doc file separated by `---`.
4. Dry-run validate before applying: `kubectl apply -f <file> --dry-run=server`.
5. Apply: `kubectl apply -f <file_or_dir>`. Use `-k <dir>` for kustomize.
6. Watch rollout: `kubectl rollout status deploy/<name> -n <ns>`.
7. To debug, read live state with read_file on saved `kubectl get -o yaml` output,
   or inspect directly (see Quick Reference). Check events first — they name the cause.

## Quick Reference
```bash
kubectl get pods -n <ns> -o wide              # status + node
kubectl describe pod <pod> -n <ns>            # events at bottom = root cause
kubectl logs <pod> -n <ns> [-c <ctr>] [-p]    # -p = previous crashed container
kubectl get events -n <ns> --sort-by=.lastTimestamp
kubectl exec -it <pod> -n <ns> -- sh
kubectl port-forward svc/<svc> 8080:80 -n <ns>
kubectl rollout undo deploy/<name> -n <ns>    # revert bad deploy
kubectl set image deploy/<name> app=img:tag -n <ns>
```
Minimal Deployment + Service skeleton:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata: {name: web, namespace: app}
spec:
  replicas: 2
  selector: {matchLabels: {app: web}}
  template:
    metadata: {labels: {app: web}}
    spec:
      containers:
      - name: web
        image: nginx:1.27
        ports: [{containerPort: 80}]
---
apiVersion: v1
kind: Service
metadata: {name: web, namespace: app}
spec:
  selector: {app: web}
  ports: [{port: 80, targetPort: 80}]
```

## Pitfalls
- Service `selector` MUST match the Pod template `labels`, not the Deployment name.
- `targetPort` is the container port; `port` is the Service's. Mismatches drop traffic.
- ImagePullBackOff = wrong image/tag or missing registry secret, not a code bug.
- CrashLoopBackOff: read `logs -p` (previous instance), not current.
- Editing a live object with `kubectl edit` is lost on next `apply`; change the manifest.
- ConfigMap/Secret changes do NOT restart pods — `kubectl rollout restart deploy/<name>`.

## Verification
- `kubectl rollout status` returns "successfully rolled out".
- `kubectl get pods -n <ns>` shows all Running/Ready (no restarts climbing).
- Reach the app via `port-forward` or Ingress and get an expected response.
