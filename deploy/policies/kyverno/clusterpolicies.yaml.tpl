apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: bretter-require-immutable-image-tags
spec:
  validationFailureAction: Enforce
  background: true
  rules:
    - name: disallow-mutable-image-tags
      match:
        any:
          - resources:
              kinds:
                - Pod
              namespaces:
                - __NAMESPACE__
              selector:
                matchLabels:
                  security.bretter-labs.io/enforce-admission: "true"
      validate:
        message: "Mutable image tags (:latest/:edge) are not permitted on enforced Bretter workloads."
        foreach:
          - list: "request.object.spec.containers[]"
            deny:
              conditions:
                any:
                  - key: "{{ regex_match('^.*:(latest|edge)$', element.image) }}"
                    operator: Equals
                    value: true
          - list: "request.object.spec.initContainers[]"
            deny:
              conditions:
                any:
                  - key: "{{ regex_match('^.*:(latest|edge)$', element.image) }}"
                    operator: Equals
                    value: true
---
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: bretter-require-pod-security-context
spec:
  validationFailureAction: Enforce
  background: true
  rules:
    - name: require-pod-level-security-context
      match:
        any:
          - resources:
              kinds:
                - Pod
              namespaces:
                - __NAMESPACE__
              selector:
                matchLabels:
                  security.bretter-labs.io/enforce-admission: "true"
      validate:
        message: "Enforced Bretter workloads must set pod securityContext with runAsNonRoot and RuntimeDefault seccomp."
        pattern:
          spec:
            securityContext:
              runAsNonRoot: true
              seccompProfile:
                type: RuntimeDefault
    - name: require-container-level-security-context
      match:
        any:
          - resources:
              kinds:
                - Pod
              namespaces:
                - __NAMESPACE__
              selector:
                matchLabels:
                  security.bretter-labs.io/enforce-admission: "true"
      validate:
        message: "Enforced Bretter workloads must run as non-root, disable privilege escalation, and drop all Linux capabilities."
        foreach:
          - list: "request.object.spec.containers[]"
            pattern:
              securityContext:
                runAsNonRoot: true
                allowPrivilegeEscalation: false
                capabilities:
                  drop:
                    - ALL
          - list: "request.object.spec.initContainers[]"
            pattern:
              securityContext:
                runAsNonRoot: true
                allowPrivilegeEscalation: false
                capabilities:
                  drop:
                    - ALL
---
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: bretter-require-container-resources
spec:
  validationFailureAction: Enforce
  background: true
  rules:
    - name: require-resources-on-containers
      match:
        any:
          - resources:
              kinds:
                - Pod
              namespaces:
                - __NAMESPACE__
              selector:
                matchLabels:
                  security.bretter-labs.io/enforce-admission: "true"
      validate:
        message: "Enforced Bretter workloads must declare CPU and memory requests/limits on all containers and initContainers."
        foreach:
          - list: "request.object.spec.containers[]"
            pattern:
              resources:
                requests:
                  cpu: "?*"
                  memory: "?*"
                limits:
                  cpu: "?*"
                  memory: "?*"
          - list: "request.object.spec.initContainers[]"
            pattern:
              resources:
                requests:
                  cpu: "?*"
                  memory: "?*"
                limits:
                  cpu: "?*"
                  memory: "?*"
