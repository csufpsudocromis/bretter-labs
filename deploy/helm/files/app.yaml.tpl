apiVersion: v1
kind: ServiceAccount
metadata:
  name: bretter-backend
  namespace: __NAMESPACE__
---
apiVersion: v1
kind: LimitRange
metadata:
  name: bretter-default-container-limits
  namespace: __NAMESPACE__
spec:
  limits:
    - type: Container
      min:
        cpu: 50m
        memory: 64Mi
      defaultRequest:
        cpu: 250m
        memory: 256Mi
      default:
        cpu: "2"
        memory: 2Gi
      max:
        cpu: "8"
        memory: 16Gi
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: bretter-runtime-quota
  namespace: __NAMESPACE__
spec:
  hard:
    pods: "400"
    services: "200"
    services.nodeports: "120"
    persistentvolumeclaims: "400"
    requests.cpu: "32"
    requests.memory: 48Gi
    limits.cpu: "64"
    limits.memory: 96Gi
    requests.storage: 10Ti
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: bretter-backend
  namespace: __NAMESPACE__
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "pods/exec", "services", "persistentvolumeclaims", "events"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["networkpolicies", "ingresses"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["cdi.kubevirt.io"]
    resources: ["datavolumes"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["upload.cdi.kubevirt.io"]
    resources: ["uploadtokenrequests"]
    verbs: ["get", "list", "watch", "create", "delete"]
  - apiGroups: ["labs.bretter.io"]
    resources: ["labinstances", "labinstances/status", "labimageimports", "labimageimports/status"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["coordination.k8s.io"]
    resources: ["leases"]
    verbs: ["get", "list", "watch", "create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: bretter-backend
  namespace: __NAMESPACE__
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: bretter-backend
subjects:
  - kind: ServiceAccount
    name: bretter-backend
    namespace: __NAMESPACE__
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: bretter-backend
rules:
  - apiGroups: [""]
    resources: ["namespaces", "nodes", "pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["create", "patch", "update"]
  - apiGroups: [""]
    resources: ["configmaps", "secrets", "resourcequotas", "limitranges"]
    verbs: ["get", "list", "watch", "create", "patch", "update"]
  - apiGroups: [""]
    resources: ["pods", "pods/log", "pods/exec", "services", "persistentvolumeclaims", "events"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["rbac.authorization.k8s.io"]
    resources: ["roles", "rolebindings"]
    verbs: ["get", "list", "watch", "create", "patch", "update"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["networkpolicies"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["cdi.kubevirt.io"]
    resources: ["datavolumes"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["upload.cdi.kubevirt.io"]
    resources: ["uploadtokenrequests"]
    verbs: ["get", "list", "watch", "create", "delete"]
  - apiGroups: ["apiextensions.k8s.io"]
    resources: ["customresourcedefinitions"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["metrics.k8s.io"]
    resources: ["nodes", "pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["storage.k8s.io"]
    resources: ["storageclasses"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["longhorn.io"]
    resources: ["nodes", "volumes"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: bretter-backend
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: bretter-backend
subjects:
  - kind: ServiceAccount
    name: bretter-backend
    namespace: __NAMESPACE__
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: backend-postgres-pv
spec:
  capacity:
    storage: 20Gi
  accessModes:
    - ReadWriteOnce
  storageClassName: backend-postgres-sc
  persistentVolumeReclaimPolicy: Retain
  volumeMode: Filesystem
  hostPath:
    path: __POSTGRES_DATA_HOSTPATH__
    type: DirectoryOrCreate
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values:
                - __CONTROL_NODE__
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: backend-postgres-data
  namespace: __NAMESPACE__
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: backend-postgres-sc
  resources:
    requests:
      storage: 20Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bretter-postgres
  namespace: __NAMESPACE__
spec:
  strategy:
    type: Recreate
  replicas: 1
  selector:
    matchLabels:
      app: bretter-postgres
  template:
    metadata:
      labels:
        app: bretter-postgres
        security.bretter-labs.io/enforce-admission: "true"
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        runAsGroup: 999
        fsGroup: 999
        fsGroupChangePolicy: OnRootMismatch
        seccompProfile:
          type: RuntimeDefault
      nodeSelector:
        kubernetes.io/hostname: __CONTROL_NODE__
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      containers:
        - name: postgres
          image: postgres:16
          imagePullPolicy: IfNotPresent
          securityContext:
            runAsNonRoot: true
            runAsUser: 999
            runAsGroup: 999
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            capabilities:
              drop:
                - ALL
          ports:
            - containerPort: 5432
          env:
            - name: POSTGRES_USER
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: POSTGRES_USER
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: POSTGRES_PASSWORD
            - name: POSTGRES_DB
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: POSTGRES_DB
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              cpu: "1"
              memory: 1Gi
          startupProbe:
            exec:
              command:
                - /bin/sh
                - -c
                - pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
            periodSeconds: 5
            timeoutSeconds: 3
            failureThreshold: 60
          readinessProbe:
            exec:
              command:
                - /bin/sh
                - -c
                - pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
            periodSeconds: 10
            timeoutSeconds: 3
            failureThreshold: 3
          livenessProbe:
            exec:
              command:
                - /bin/sh
                - -c
                - pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
            periodSeconds: 20
            timeoutSeconds: 3
            failureThreshold: 3
          volumeMounts:
            - name: pgdata
              mountPath: /var/lib/postgresql/data
      volumes:
        - name: pgdata
          persistentVolumeClaim:
            claimName: backend-postgres-data
---
apiVersion: v1
kind: Service
metadata:
  name: bretter-postgres
  namespace: __NAMESPACE__
spec:
  type: ClusterIP
  selector:
    app: bretter-postgres
  ports:
    - port: 5432
      targetPort: 5432
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: backend-data-pv
spec:
  capacity:
    storage: 10Gi
  accessModes:
    - ReadWriteOnce
  storageClassName: backend-data-sc
  persistentVolumeReclaimPolicy: Retain
  volumeMode: Filesystem
  hostPath:
    path: __BACKEND_DATA_HOSTPATH__
    type: DirectoryOrCreate
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values:
                - __CONTROL_NODE__
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: backend-data
  namespace: __NAMESPACE__
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: backend-data-sc
  resources:
    requests:
      storage: 10Gi
---
apiVersion: batch/v1
kind: Job
metadata:
  name: bretter-db-migrate
  namespace: __NAMESPACE__
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "-10"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  ttlSecondsAfterFinished: 3600
  backoffLimit: 3
  template:
    metadata:
      labels:
        app: bretter-db-migrate
        security.bretter-labs.io/enforce-admission: "true"
    spec:
      restartPolicy: Never
      serviceAccountName: bretter-backend
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        fsGroupChangePolicy: OnRootMismatch
        seccompProfile:
          type: RuntimeDefault
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      imagePullSecrets:
        - name: ghcr-creds
      initContainers:
        - name: wait-for-postgres
          image: postgres:16
          imagePullPolicy: IfNotPresent
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            capabilities:
              drop:
                - ALL
          command:
            - /bin/sh
            - -c
            - until pg_isready -h bretter-postgres.__NAMESPACE__.svc.cluster.local -p 5432 -U "$POSTGRES_USER" -d "$POSTGRES_DB"; do sleep 2; done
          env:
            - name: POSTGRES_USER
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: POSTGRES_USER
            - name: POSTGRES_DB
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: POSTGRES_DB
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: POSTGRES_PASSWORD
      containers:
        - name: migrate
          image: __BACKEND_IMAGE__
          imagePullPolicy: IfNotPresent
          command:
            - python
            - -c
            - from backend.src.db import init_db; init_db()
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            capabilities:
              drop:
                - ALL
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
              ephemeral-storage: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
              ephemeral-storage: 1Gi
          env:
            - name: BLABS_DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: BLABS_DATABASE_URL
            - name: BLABS_REQUIRE_SCHEMA_READY
              value: "__REQUIRE_SCHEMA_READY__"
            - name: BLABS_EXPECTED_ALEMBIC_REVISION
              value: "__EXPECTED_ALEMBIC_REVISION__"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bretter-backend
  namespace: __NAMESPACE__
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1
      maxSurge: 0
  replicas: __BACKEND_REPLICAS__
  selector:
    matchLabels:
      app: bretter-backend
  template:
    metadata:
      labels:
        app: bretter-backend
        security.bretter-labs.io/enforce-admission: "true"
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        fsGroupChangePolicy: OnRootMismatch
        seccompProfile:
          type: RuntimeDefault
      affinity:
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            - labelSelector:
                matchLabels:
                  app: bretter-backend
              topologyKey: kubernetes.io/hostname
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app: bretter-backend
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      serviceAccountName: bretter-backend
      imagePullSecrets:
        - name: ghcr-creds
      initContainers:
        - name: wait-for-postgres
          image: postgres:16
          imagePullPolicy: IfNotPresent
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            capabilities:
              drop:
                - ALL
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
              ephemeral-storage: 64Mi
            limits:
              cpu: 100m
              memory: 128Mi
              ephemeral-storage: 256Mi
          command:
            - /bin/sh
            - -c
            - until pg_isready -h bretter-postgres.__NAMESPACE__.svc.cluster.local -p 5432 -U "$POSTGRES_USER" -d "$POSTGRES_DB"; do sleep 2; done
          env:
            - name: POSTGRES_USER
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: POSTGRES_USER
            - name: POSTGRES_DB
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: POSTGRES_DB
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: POSTGRES_PASSWORD
      containers:
        - name: backend
          image: __BACKEND_IMAGE__
          imagePullPolicy: IfNotPresent
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            capabilities:
              drop:
                - ALL
          ports:
            - containerPort: 8000
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
              ephemeral-storage: 2Gi
            limits:
              cpu: "1"
              memory: 2Gi
              ephemeral-storage: 8Gi
          startupProbe:
            tcpSocket:
              port: 8000
            periodSeconds: 5
            timeoutSeconds: 2
            failureThreshold: 60
          readinessProbe:
            tcpSocket:
              port: 8000
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          livenessProbe:
            tcpSocket:
              port: 8000
            periodSeconds: 20
            timeoutSeconds: 2
            failureThreshold: 3
          env:
            - name: BLABS_KUBE_NAMESPACE
              value: __NAMESPACE__
            - name: BLABS_KUBE_IMAGE_PVC
              value: golden-images
            - name: BLABS_KUBE_VM_STORAGE_CLASS
              value: "__VM_STORAGE_CLASS__"
            - name: BLABS_KUBE_UPLOAD_USE_CDI
              value: "true"
            - name: BLABS_CDI_DIRECT_UPLOAD_ENABLED
              value: "true"
            - name: BLABS_CDI_UPLOAD_PROXY_URL
              value: "__CDI_UPLOAD_PROXY_URL__"
            - name: BLABS_CDI_UPLOAD_SOURCE_FILENAME
              value: "disk.img"
            - name: BLABS_STORAGE_ROOT
              value: /mnt/lab-images
            - name: BLABS_DATABASE_PATH
              value: /data/app.db
            - name: BLABS_DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: BLABS_DATABASE_URL
            - name: BLABS_DATABASE_POOL_SIZE
              value: "__DATABASE_POOL_SIZE__"
            - name: BLABS_DATABASE_POOL_MAX_OVERFLOW
              value: "__DATABASE_POOL_MAX_OVERFLOW__"
            - name: BLABS_DATABASE_POOL_TIMEOUT_SECONDS
              value: "__DATABASE_POOL_TIMEOUT_SECONDS__"
            - name: BLABS_DATABASE_POOL_RECYCLE_SECONDS
              value: "__DATABASE_POOL_RECYCLE_SECONDS__"
            - name: BLABS_DATABASE_STATEMENT_TIMEOUT_MS
              value: "__DATABASE_STATEMENT_TIMEOUT_MS__"
            - name: BLABS_DATABASE_SLOW_QUERY_MS
              value: "__DATABASE_SLOW_QUERY_MS__"
            - name: BLABS_DB_AUTO_MIGRATE_ON_STARTUP
              value: "__DB_AUTO_MIGRATE_ON_STARTUP__"
            - name: BLABS_ADMIN_DEFAULT_PASSWORD
              value: "__ADMIN_BOOTSTRAP_PASSWORD__"
            - name: BLABS_ERROR_LOG_FILE_PATH
              value: /data/error.log
            - name: BLABS_ERROR_LOG_MAX_BYTES
              value: "10485760"
            - name: BLABS_API_DOCS_ENABLED
              value: "false"
            - name: UVICORN_WORKERS
              value: "__UVICORN_WORKERS__"
            - name: BLABS_KUBE_NODE_EXTERNAL_HOST
              value: __NODE_EXTERNAL_HOST__
            - name: BLABS_PUBLIC_SCHEME
              value: __PUBLIC_SCHEME__
            - name: BLABS_KUBE_TLS_SECRET
              value: __TLS_SECRET_NAME__
            - name: BLABS_ORCHESTRATION_BACKEND
              value: "__ORCHESTRATION_BACKEND__"
            - name: BLABS_IMAGE_IMPORT_BACKEND
              value: "__IMAGE_IMPORT_BACKEND__"
            - name: BLABS_LABINSTANCE_CRD_GROUP
              value: "__LABINSTANCE_CRD_GROUP__"
            - name: BLABS_LABINSTANCE_CRD_VERSION
              value: "__LABINSTANCE_CRD_VERSION__"
            - name: BLABS_LABINSTANCE_CRD_PLURAL
              value: "__LABINSTANCE_CRD_PLURAL__"
            - name: BLABS_LABINSTANCE_CRD_FINALIZER
              value: "__LABINSTANCE_CRD_FINALIZER__"
            - name: BLABS_LABIMAGEIMPORT_CRD_GROUP
              value: "__LABIMAGEIMPORT_CRD_GROUP__"
            - name: BLABS_LABIMAGEIMPORT_CRD_VERSION
              value: "__LABIMAGEIMPORT_CRD_VERSION__"
            - name: BLABS_LABIMAGEIMPORT_CRD_PLURAL
              value: "__LABIMAGEIMPORT_CRD_PLURAL__"
            - name: BLABS_LABIMAGEIMPORT_CRD_FINALIZER
              value: "__LABIMAGEIMPORT_CRD_FINALIZER__"
            - name: BLABS_TEAM_NAMESPACE_MODE
              value: "__TEAM_NAMESPACE_MODE__"
            - name: BLABS_TEAM_NAMESPACE_PREFIX
              value: "__TEAM_NAMESPACE_PREFIX__"
            - name: BLABS_TEAM_NAMESPACE_BOOTSTRAP_ENABLED
              value: "__TEAM_NAMESPACE_BOOTSTRAP_ENABLED__"
            - name: BLABS_BACKEND_IMAGE
              value: "__BACKEND_IMAGE__"
            - name: BLABS_BACKEND_ADMIN_IMAGE
              value: "__BACKEND_ADMIN_IMAGE__"
            - name: BLABS_KUBE_NODE_SELECTOR_VALUE
              value: "__RUNNER_NODE_SELECTOR_VALUE__"
            - name: BLABS_RUNNER_IMAGE
              value: __RUNNER_IMAGE__
            - name: BLABS_IMAGE_PULL_SECRET
              value: ghcr-creds
            - name: BLABS_WINDOWS_MACHINE_TYPE
              value: __WINDOWS_MACHINE_TYPE__
            - name: BLABS_WINDOWS_EFI_ENABLED
              value: "__WINDOWS_EFI_ENABLED__"
            - name: BLABS_WINDOWS_CPU_MODEL
              value: __WINDOWS_CPU_MODEL__
            - name: BLABS_LINUX_MACHINE_TYPE
              value: __LINUX_MACHINE_TYPE__
            - name: BLABS_LINUX_EFI_ENABLED
              value: "__LINUX_EFI_ENABLED__"
            - name: BLABS_LINUX_CPU_MODEL
              value: __LINUX_CPU_MODEL__
            - name: BLABS_KUBE_USE_KVM
              value: "__KUBE_USE_KVM__"
            - name: BLABS_VM_NET_BACKEND
              value: __VM_NET_BACKEND__
            - name: BLABS_VM_RUNNER_PRIVILEGED
              value: "__VM_RUNNER_PRIVILEGED__"
            - name: BLABS_VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED
              value: "__VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED__"
            - name: BLABS_VM_PRIVILEGED_NAMESPACE_PREFIX
              value: "__VM_PRIVILEGED_NAMESPACE_PREFIX__"
            - name: BLABS_VM_CONSOLE_EXTERNAL_TRAFFIC_POLICY
              value: "__VM_CONSOLE_EXTERNAL_TRAFFIC_POLICY__"
            - name: BLABS_VM_CONSOLE_SOURCE_CIDRS
              value: "__VM_CONSOLE_SOURCE_CIDRS__"
            - name: BLABS_VM_CONSOLE_TICKET_LENGTH
              value: "__VM_CONSOLE_TICKET_LENGTH__"
            - name: BLABS_VM_VHOST_NET_ENABLED
              value: "true"
            - name: BLABS_VM_NET_MULTIQUEUE_ENABLED
              value: "true"
            - name: BLABS_VM_QOS_GUARANTEED
              value: "true"
            - name: BLABS_VM_MEMORY_OVERHEAD_MB
              value: "1024"
            - name: BLABS_VM_RUNNER_TOPOLOGY_SPREAD_ENABLED
              value: "true"
            - name: BLABS_VM_RUNNER_ANTI_AFFINITY_ENABLED
              value: "true"
            - name: BLABS_CONTAINER_INGRESS_ENABLED
              value: "__CONTAINER_INGRESS_ENABLED__"
            - name: BLABS_CONTAINER_INGRESS_CLASS
              value: "__CONTAINER_INGRESS_CLASS__"
            - name: BLABS_CONTAINER_INGRESS_BASE_DOMAIN
              value: "__CONTAINER_INGRESS_BASE_DOMAIN__"
            - name: BLABS_CONTAINER_INGRESS_ANNOTATIONS_JSON
              value: "__CONTAINER_INGRESS_ANNOTATIONS_JSON__"
            - name: BLABS_CONTAINER_IMAGE_PREPULL_ENABLED
              value: "__CONTAINER_IMAGE_PREPULL_ENABLED__"
            - name: BLABS_CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS
              value: "__CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS__"
            - name: BLABS_CONTAINER_ALLOWED_REGISTRIES
              value: "__CONTAINER_ALLOWED_REGISTRIES__"
            - name: BLABS_CONTAINER_SIGNATURE_VERIFICATION_ENABLED
              value: "__CONTAINER_SIGNATURE_VERIFICATION_ENABLED__"
            - name: BLABS_CONTAINER_SIGNATURE_KEY_REF
              value: "__CONTAINER_SIGNATURE_KEY_REF__"
            - name: BLABS_CONTAINER_SCAN_ENABLED
              value: "__CONTAINER_SCAN_ENABLED__"
            - name: BLABS_CONTAINER_SCAN_INTERVAL_MINUTES
              value: "__CONTAINER_SCAN_INTERVAL_MINUTES__"
            - name: BLABS_CONTAINER_SCAN_SEVERITY
              value: "__CONTAINER_SCAN_SEVERITY__"
            - name: BLABS_CONTAINER_START_QUEUE_ENABLED
              value: "__CONTAINER_START_QUEUE_ENABLED__"
            - name: BLABS_CONTAINER_START_QUEUE_BASE_DELAY_SECONDS
              value: "__CONTAINER_START_QUEUE_BASE_DELAY_SECONDS__"
            - name: BLABS_CONTAINER_START_QUEUE_MAX_DELAY_SECONDS
              value: "__CONTAINER_START_QUEUE_MAX_DELAY_SECONDS__"
            - name: BLABS_PRODUCTION_PROFILE
              value: "__PRODUCTION_PROFILE__"
            - name: BLABS_ALLOW_CODE_MOUNT_OVERRIDES
              value: "__ALLOW_CODE_MOUNT_OVERRIDES__"
            - name: BLABS_REQUIRE_SCHEMA_READY
              value: "__REQUIRE_SCHEMA_READY__"
            - name: BLABS_EXPECTED_ALEMBIC_REVISION
              value: "__EXPECTED_ALEMBIC_REVISION__"
            - name: BLABS_CORS_ENTERPRISE_PROFILE
              value: "__CORS_ENTERPRISE_PROFILE__"
            - name: BLABS_CORS_ALLOWED_ORIGINS
              value: "__CORS_ALLOWED_ORIGINS__"
            - name: BLABS_CORS_ALLOWED_ORIGIN_REGEX
              value: "__CORS_ALLOWED_ORIGIN_REGEX__"
            - name: BLABS_CORS_ALLOWED_METHODS
              value: "__CORS_ALLOWED_METHODS__"
            - name: BLABS_CORS_ALLOWED_HEADERS
              value: "__CORS_ALLOWED_HEADERS__"
            - name: BLABS_AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS
              value: "__AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS__"
            - name: BLABS_AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS
              value: "__AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS__"
            - name: BLABS_AUTH_LOGIN_LOCKOUT_SECONDS
              value: "__AUTH_LOGIN_LOCKOUT_SECONDS__"
            - name: BLABS_VM_CONNECT_INSECURE_TLS
              value: "__VM_CONNECT_INSECURE_TLS__"
            - name: BLABS_CONTAINER_CONNECT_INSECURE_TLS
              value: "__CONTAINER_CONNECT_INSECURE_TLS__"
            - name: BLABS_SECRETS_ENCRYPTION_KEY
              valueFrom:
                secretKeyRef:
                  name: __RUNTIME_SECRETS_SECRET_NAME__
                  key: __RUNTIME_SECRETS_ENCRYPTION_KEY_KEY__
                  optional: true
          volumeMounts:
            - name: images
              mountPath: /mnt/lab-images
            - name: data
              mountPath: /data
            - name: tls-cert
              mountPath: /tls
              readOnly: true
            - name: container-signature-key
              mountPath: /etc/bretter-signing
              readOnly: true
      volumes:
        - name: images
          persistentVolumeClaim:
            claimName: golden-images
        - name: data
          emptyDir: {}
        - name: tls-cert
          secret:
            secretName: __TLS_SECRET_NAME__
            optional: true
        - name: container-signature-key
          secret:
            secretName: __CONTAINER_SIGNATURE_KEY_SECRET_NAME__
            optional: true
---
apiVersion: v1
kind: Service
metadata:
  name: bretter-backend
  namespace: __NAMESPACE__
  labels:
    app: bretter-backend
spec:
  type: __BACKEND_SERVICE_TYPE__
  selector:
    app: bretter-backend
  ports:
    - name: http
      port: 8000
      targetPort: 8000
__BACKEND_SERVICE_NODEPORT_LINE__
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: bretter-backend
  namespace: __NAMESPACE__
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: bretter-backend
  minReplicas: __BACKEND_HPA_MIN_REPLICAS__
  maxReplicas: __BACKEND_HPA_MAX_REPLICAS__
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0
      selectPolicy: Max
      policies:
        - type: Percent
          value: 100
          periodSeconds: 60
        - type: Pods
          value: 2
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      selectPolicy: Max
      policies:
        - type: Percent
          value: 50
          periodSeconds: 60
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: __BACKEND_HPA_TARGET_CPU_UTILIZATION_PERCENT__
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bretter-labimageimport-controller
  namespace: __NAMESPACE__
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1
      maxSurge: 0
  replicas: 1
  selector:
    matchLabels:
      app: bretter-labimageimport-controller
  template:
    metadata:
      labels:
        app: bretter-labimageimport-controller
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        fsGroupChangePolicy: OnRootMismatch
        seccompProfile:
          type: RuntimeDefault
      serviceAccountName: bretter-backend
      imagePullSecrets:
        - name: ghcr-creds
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      containers:
        - name: controller
          image: __BACKEND_IMAGE__
          imagePullPolicy: IfNotPresent
          command:
            - python
            - -m
            - backend.src.tools.labimageimport_controller
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            capabilities:
              drop:
                - ALL
          ports:
            - name: metrics
              containerPort: __LABIMAGEIMPORT_CONTROLLER_METRICS_PORT__
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
              ephemeral-storage: 512Mi
            limits:
              cpu: 500m
              memory: 512Mi
              ephemeral-storage: 2Gi
          startupProbe:
            httpGet:
              path: /livez
              port: metrics
            periodSeconds: 5
            timeoutSeconds: 2
            failureThreshold: 60
          readinessProbe:
            httpGet:
              path: /readyz
              port: metrics
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /livez
              port: metrics
            periodSeconds: 20
            timeoutSeconds: 2
            failureThreshold: 3
          env:
            - name: BLABS_KUBE_NAMESPACE
              value: __NAMESPACE__
            - name: BLABS_DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: bretter-postgres
                  key: BLABS_DATABASE_URL
            - name: BLABS_KUBE_IMAGE_PVC
              value: golden-images
            - name: BLABS_RUNNER_IMAGE
              value: __RUNNER_IMAGE__
            - name: BLABS_IMAGE_PULL_SECRET
              value: ghcr-creds
            - name: BLABS_IMAGE_IMPORT_BACKEND
              value: "__IMAGE_IMPORT_BACKEND__"
            - name: BLABS_LABIMAGEIMPORT_CRD_GROUP
              value: "__LABIMAGEIMPORT_CRD_GROUP__"
            - name: BLABS_LABIMAGEIMPORT_CRD_VERSION
              value: "__LABIMAGEIMPORT_CRD_VERSION__"
            - name: BLABS_LABIMAGEIMPORT_CRD_PLURAL
              value: "__LABIMAGEIMPORT_CRD_PLURAL__"
            - name: BLABS_LABIMAGEIMPORT_CRD_FINALIZER
              value: "__LABIMAGEIMPORT_CRD_FINALIZER__"
            - name: BLABS_LABIMAGEIMPORT_CONTROLLER_ENABLED
              value: "__LABIMAGEIMPORT_CONTROLLER_ENABLED__"
            - name: BLABS_LABIMAGEIMPORT_CONTROLLER_LEADER_ELECTION_ENABLED
              value: "__LABIMAGEIMPORT_CONTROLLER_LEADER_ELECTION_ENABLED__"
            - name: BLABS_LABIMAGEIMPORT_CONTROLLER_LEASE_NAME
              value: "__LABIMAGEIMPORT_CONTROLLER_LEASE_NAME__"
            - name: BLABS_LABIMAGEIMPORT_CONTROLLER_LEASE_DURATION_SECONDS
              value: "__LABIMAGEIMPORT_CONTROLLER_LEASE_DURATION_SECONDS__"
            - name: BLABS_LABIMAGEIMPORT_CONTROLLER_RETRY_PERIOD_SECONDS
              value: "__LABIMAGEIMPORT_CONTROLLER_RETRY_PERIOD_SECONDS__"
            - name: BLABS_LABIMAGEIMPORT_CONTROLLER_POLL_SECONDS
              value: "__LABIMAGEIMPORT_CONTROLLER_POLL_SECONDS__"
            - name: BLABS_LABIMAGEIMPORT_CONTROLLER_METRICS_BIND
              value: "__LABIMAGEIMPORT_CONTROLLER_METRICS_BIND__"
            - name: BLABS_LABIMAGEIMPORT_CONTROLLER_METRICS_PORT
              value: "__LABIMAGEIMPORT_CONTROLLER_METRICS_PORT__"
            - name: BLABS_DB_AUTO_MIGRATE_ON_STARTUP
              value: "__DB_AUTO_MIGRATE_ON_STARTUP__"
          volumeMounts:
            - name: images
              mountPath: /mnt/lab-images
      volumes:
        - name: images
          persistentVolumeClaim:
            claimName: golden-images
---
apiVersion: v1
kind: Service
metadata:
  name: bretter-labimageimport-controller
  namespace: __NAMESPACE__
spec:
  type: ClusterIP
  selector:
    app: bretter-labimageimport-controller
  ports:
    - name: metrics
      port: __LABIMAGEIMPORT_CONTROLLER_METRICS_PORT__
      targetPort: metrics
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bretter-frontend
  namespace: __NAMESPACE__
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1
      maxSurge: 0
  replicas: __FRONTEND_REPLICAS__
  selector:
    matchLabels:
      app: bretter-frontend
  template:
    metadata:
      labels:
        app: bretter-frontend
        security.bretter-labs.io/enforce-admission: "true"
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 101
        runAsGroup: 101
        fsGroup: 101
        fsGroupChangePolicy: OnRootMismatch
        seccompProfile:
          type: RuntimeDefault
      affinity:
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            - labelSelector:
                matchLabels:
                  app: bretter-frontend
              topologyKey: kubernetes.io/hostname
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app: bretter-frontend
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      imagePullSecrets:
        - name: ghcr-creds
      containers:
        - name: frontend
          image: __FRONTEND_IMAGE__
          imagePullPolicy: IfNotPresent
          securityContext:
            runAsNonRoot: true
            runAsUser: 101
            runAsGroup: 101
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            capabilities:
              drop:
                - ALL
          ports:
            - containerPort: 8443
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
              ephemeral-storage: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
              ephemeral-storage: 1Gi
          startupProbe:
            tcpSocket:
              port: 8443
            periodSeconds: 5
            timeoutSeconds: 2
            failureThreshold: 60
          readinessProbe:
            tcpSocket:
              port: 8443
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          livenessProbe:
            tcpSocket:
              port: 8443
            periodSeconds: 20
            timeoutSeconds: 2
            failureThreshold: 3
          volumeMounts:
            - name: tls-cert
              mountPath: /tls
              readOnly: true
      volumes:
        - name: tls-cert
          secret:
            secretName: __TLS_SECRET_NAME__
            optional: true
---
apiVersion: v1
kind: Service
metadata:
  name: bretter-frontend
  namespace: __NAMESPACE__
spec:
  type: NodePort
  selector:
    app: bretter-frontend
  ports:
    - port: 443
      targetPort: 8443
      nodePort: 30073
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: bretter-frontend
  namespace: __NAMESPACE__
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: bretter-frontend
  minReplicas: __FRONTEND_HPA_MIN_REPLICAS__
  maxReplicas: __FRONTEND_HPA_MAX_REPLICAS__
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0
      selectPolicy: Max
      policies:
        - type: Percent
          value: 100
          periodSeconds: 60
        - type: Pods
          value: 3
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      selectPolicy: Max
      policies:
        - type: Percent
          value: 50
          periodSeconds: 60
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: __FRONTEND_HPA_TARGET_CPU_UTILIZATION_PERCENT__
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: bretter-default-deny-ingress
  namespace: __NAMESPACE__
spec:
  podSelector: {}
  policyTypes:
    - Ingress
  ingress: []
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: bretter-backend-allow-ingress
  namespace: __NAMESPACE__
spec:
  podSelector:
    matchLabels:
      app: bretter-backend
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: __NAMESPACE__
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
      ports:
        - protocol: TCP
          port: 8000
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: bretter-frontend-allow-ingress
  namespace: __NAMESPACE__
spec:
  podSelector:
    matchLabels:
      app: bretter-frontend
  policyTypes:
    - Ingress
  ingress:
    - from:
        - ipBlock:
            cidr: 0.0.0.0/0
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
      ports:
        - protocol: TCP
          port: 8443
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: bretter-labimageimport-controller-allow-ingress
  namespace: __NAMESPACE__
spec:
  podSelector:
    matchLabels:
      app: bretter-labimageimport-controller
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: __NAMESPACE__
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
      ports:
        - protocol: TCP
          port: __LABIMAGEIMPORT_CONTROLLER_METRICS_PORT__
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: bretter-frontend-restrict-egress
  namespace: __NAMESPACE__
spec:
  podSelector:
    matchLabels:
      app: bretter-frontend
  policyTypes:
    - Egress
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: bretter-backend
      ports:
        - protocol: TCP
          port: 8000
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: bretter-backend-restrict-egress
  namespace: __NAMESPACE__
spec:
  podSelector:
    matchLabels:
      app: bretter-backend
  policyTypes:
    - Egress
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: bretter-postgres
      ports:
        - protocol: TCP
          port: 5432
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: __NAMESPACE__
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
      ports:
        - protocol: TCP
          port: 443
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: bretter-postgres-allow-backend
  namespace: __NAMESPACE__
spec:
  podSelector:
    matchLabels:
      app: bretter-postgres
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: bretter-backend
      ports:
        - protocol: TCP
          port: 5432
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: bretter-backend
  namespace: __NAMESPACE__
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: bretter-backend
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: bretter-frontend
  namespace: __NAMESPACE__
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: bretter-frontend
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: bretter-labimageimport-controller
  namespace: __NAMESPACE__
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: bretter-labimageimport-controller
