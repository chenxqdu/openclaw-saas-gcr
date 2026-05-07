apiVersion: agents.x-k8s.io/v1alpha1
kind: Sandbox
metadata:
  name: hermes-feishu-sandbox
  namespace: hermes
spec:
  podTemplate:
    metadata:
      labels:
        sandbox: hermes-feishu-sandbox
    spec:
      automountServiceAccountToken: true
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      # Schedule onto the Karpenter-managed sandbox nodepool only.
      # Reference uses kata-qemu nodes; this workshop skips kata and
      # relies on the dedicated nodepool for workload isolation.
      nodeSelector:
        workload-type: sandbox
      tolerations:
        - key: sandbox
          operator: Equal
          value: "true"
          effect: NoSchedule
      containers:
        - name: hermes
          image: ${HERMES_IMAGE}
          imagePullPolicy: IfNotPresent
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            runAsNonRoot: true
            capabilities:
              drop:
                - ALL
          command:
            - sh
            - -c
            - |
              mkdir -p /mnt/workspace/.hermes
              cp /config/config.yaml /mnt/workspace/.hermes/config.yaml
              exec /opt/hermes/.venv/bin/hermes gateway
          env:
            - name: HOME
              value: "/mnt/workspace"
            - name: HERMES_HOME
              value: "/mnt/workspace/.hermes"
            - name: OPENAI_API_KEY
              valueFrom:
                secretKeyRef:
                  name: hermes-litellm-key
                  key: api-key
            - name: FEISHU_APP_ID
              valueFrom:
                secretKeyRef:
                  name: hermes-feishu
                  key: app-id
            - name: FEISHU_APP_SECRET
              valueFrom:
                secretKeyRef:
                  name: hermes-feishu
                  key: app-secret
            - name: FEISHU_DOMAIN
              value: "feishu"
            - name: FEISHU_CONNECTION_MODE
              value: "websocket"
            - name: GATEWAY_ALLOW_ALL_USERS
              value: "true"
          resources:
            requests:
              cpu: "2"
              memory: "4Gi"
            limits:
              cpu: "2"
              memory: "4Gi"
          volumeMounts:
            - mountPath: /mnt/workspace
              name: workspaces-pvc
            - mountPath: /config
              name: config
      volumes:
        - name: config
          configMap:
            name: hermes-config
  volumeClaimTemplates:
    - metadata:
        name: workspaces-pvc
      spec:
        storageClassName: "gp3"
        accessModes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 10Gi
