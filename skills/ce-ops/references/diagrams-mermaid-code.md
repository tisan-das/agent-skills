# Compute Engine — Mermaid Diagram Code

All 8 diagrams as importable Mermaid code.

---

## Diagram 1: Complete System Architecture

```mermaid
graph TB
    subgraph EXT["External / User Layer"]
        USER["User / Console UI"]
        PING["Ping Identity IDP / JWKS"]
        SNOW["ServiceNow ITSM"]
    end

    subgraph APIGW["API Gateway Layer"]
        APIGW_GC["API Gateway<br/>cog-global-compute<br/>REST + WAF"]
        APIGW_META["API Gateway<br/>accp-metadata-service<br/>REST"]
        APIGW_POOL["API Gateway<br/>pooling-service<br/>IAM Auth"]
        APIGW_NET["HTTP API Gateway<br/>accp-network-service<br/>VPC Link"]
    end

    subgraph GC["cog-global-compute — Central Orchestrator"]
        GC_AUTH["Authorizer Lambda<br/>JWT Validation<br/>256MB / 30s"]
        GC_API["API Lambda<br/>Cluster CRUD + Config<br/>1024MB / 60s"]
        GC_STATUS["Status Monitor Lambda<br/>CloudWatch Events 3min<br/>2048MB / 15min"]
        GC_PL["PrivateLink Monitor<br/>Self-invoking 30s<br/>Lambda"]
        GC_WL["Whitelist-IP Lambda<br/>Security Group Rules"]
        GC_SCHED["Scheduler<br/>ECS Fargate<br/>SQS or DDB poll"]
        GC_DDB[("DynamoDB<br/>cluster-provisioning-env<br/>PK:site_id SK:config_id")]
        GC_SQS["SQS FIFO<br/>auto-suspend-queue-env"]
    end

    subgraph META["accp-metadata-service — Config and State Store"]
        META_API["Service Lambda<br/>Config/Org/Site CRUD<br/>localhost:5005 dev"]
        META_CCP["Config Change<br/>Processor Lambda<br/>DDB Streams trigger"]
        META_EVT["Event Processor<br/>Lambda<br/>SQS metering events"]
        META_DDB_CFG[("DynamoDB<br/>compute-engine-configs<br/>5 GSIs")]
        META_DDB_ORG[("DynamoDB<br/>organizations<br/>2 GSIs")]
        META_DDB_SITE[("DynamoDB<br/>vce-sites<br/>2 GSIs")]
        META_DDB_INFRA[("DynamoDB<br/>ce-infrastructure")]
        META_DDB_CTR[("DynamoDB<br/>ce-site-id-counter<br/>Atomic counter")]
    end

    subgraph NET["accp-network-service — VPC and Networking"]
        NET_API["Network Service<br/>ECS Fargate :8080<br/>Go REST API"]
        NET_DDB_SITE[("DynamoDB<br/>network-svc-sites")]
        NET_DDB_STATUS[("DynamoDB<br/>network-svc-status<br/>TTL: 4 weeks")]
    end

    subgraph LMO["svc-vce-lmo — Lifecycle Orchestrator"]
        LMO_API["LMO API<br/>Granian :8000<br/>Litestar Python"]
        LMO_WORKER["Celery Workers<br/>ECS Fargate<br/>20+ workflow flows"]
        LMO_SQS["SQS FIFO<br/>Celery Broker"]
        LMO_REDIS[("ElastiCache<br/>Redis/Valkey 7<br/>Results + Scheduler")]
    end

    subgraph POOL["pooling-service — Pooled Clusters"]
        POOL_API["API Lambda<br/>Pool/Cluster REST<br/>512MB / 5min"]
        POOL_WORKER["Worker Lambda<br/>SQS Operations<br/>1024MB / 15min"]
        POOL_JOB["Job Lambda<br/>SQS Jobs<br/>1024MB / 15min"]
        POOL_SQS_OPS["SQS<br/>cluster-operations<br/>+ DLQ"]
        POOL_SQS_JOBS["SQS<br/>cluster-jobs<br/>+ DLQ"]
    end

    subgraph VCE["VCE Engine Build and Deploy Pipeline"]
        BOM["avcd-vce-image-bom<br/>Package BOM<br/>dev.yaml / release.yaml"]
        PACKER["avcd-vce-engine-packer<br/>Packer HCL<br/>AMI / Image Builder"]
        CONFIG["avcd-vce-engine-configure<br/>Salt States to RPMs<br/>Pre/Post Init"]
        PROVISION["avcd-vce-engine-provision<br/>Scorch Container<br/>CloudFormation Deploy"]
    end

    subgraph ENGINE["On-Engine Runtime"]
        WES["workspaces-event-scheduler<br/>gRPC:50051 REST:50061<br/>Inactivity + Telemetry"]
        DB_ENGINE["VCE Database Engine<br/>Teradata TDBMS"]
    end

    subgraph PLATFORM["Platform Services"]
        SCORCH["SCOrch Gateway"]
        CIDS["CIDS — RBAC"]
        CSM["CSM — Secrets"]
        SITEGW["Site Gateway"]
        DNS_SVC["DNS Service"]
        VALTIX["Valtix Firewall"]
        BOOST["Boost OAuth2"]
        SCIM["SCIM Identity"]
    end

    USER -->|"HTTPS REST"| APIGW_GC
    APIGW_GC --> GC_AUTH
    GC_AUTH -->|"JWKS Fetch"| PING
    APIGW_GC --> GC_API
    APIGW_META --> META_API
    APIGW_POOL --> POOL_API
    APIGW_NET --> NET_API

    GC_API -->|"GET/PATCH configs"| META_API
    GC_API -->|"POST /clusters"| POOL_API
    GC_API -->|"Component lifecycle"| SCORCH
    GC_API -->|"RBAC resolve"| CIDS
    GC_API -->|"Site resources"| SITEGW
    GC_API -->|"Async flows"| LMO_API
    GC_API -->|"Credentials"| CSM
    GC_API -->|"R/W state"| GC_DDB
    GC_API -->|"Invoke async"| GC_PL
    GC_API -->|"Invoke"| GC_WL
    GC_STATUS -->|"Scan clusters"| GC_DDB
    GC_STATUS -->|"Poll status"| SCORCH
    GC_STATUS -->|"Poll pooled"| POOL_API
    GC_SCHED -->|"Poll/Receive"| GC_SQS
    GC_SCHED -->|"R/W"| GC_DDB

    META_API -->|"VPC deploy"| NET_API
    META_API -->|"Pooled CRUD"| POOL_API
    META_API -->|"Site lifecycle"| SNOW
    META_API -->|"RBAC"| CIDS
    META_API -->|"Identity"| SCIM
    META_API -->|"Firewall"| VALTIX
    META_API -->|"OAuth2"| BOOST
    META_API --> META_DDB_CFG
    META_API --> META_DDB_ORG
    META_API --> META_DDB_SITE
    META_API --> META_DDB_INFRA
    META_API --> META_DDB_CTR
    META_CCP -->|"Stream"| META_DDB_CFG

    NET_API -->|"Cross-acct"| SITEGW
    NET_API -->|"Status callback"| META_API
    NET_API -->|"DNS records"| DNS_SVC
    NET_API -->|"Valtix deploy"| SNOW
    NET_API --> NET_DDB_SITE
    NET_API --> NET_DDB_STATUS

    LMO_API -->|"Queue tasks"| LMO_SQS
    LMO_SQS --> LMO_WORKER
    LMO_WORKER --> LMO_REDIS
    LMO_WORKER -->|"Components"| SCORCH
    LMO_WORKER -->|"App routes"| SITEGW
    LMO_WORKER -->|"Network"| NET_API
    LMO_WORKER -->|"DNS"| DNS_SVC
    LMO_WORKER -->|"Identity"| SCIM

    POOL_API --> POOL_SQS_OPS
    POOL_API --> POOL_SQS_JOBS
    POOL_SQS_OPS --> POOL_WORKER
    POOL_SQS_JOBS --> POOL_JOB

    BOM -->|"Version specs"| PACKER
    CONFIG -->|"RPM artifacts"| PACKER
    CONFIG -->|"Post-init"| PROVISION
    SCORCH -->|"Launch container"| PROVISION
    PROVISION -->|"CloudFormation + Wait"| DB_ENGINE

    WES -->|"SQL: DBC.QRYLOG"| DB_ENGINE
    GC_SQS -.->|"Suspend signal"| WES

    style EXT fill:#fef3c7,stroke:#d97706
    style GC fill:#dbeafe,stroke:#2563eb
    style META fill:#dcfce7,stroke:#16a34a
    style NET fill:#fce7f3,stroke:#db2777
    style LMO fill:#f3e8ff,stroke:#9333ea
    style POOL fill:#ffedd5,stroke:#ea580c
    style VCE fill:#e0e7ff,stroke:#4f46e5
    style ENGINE fill:#fee2e2,stroke:#dc2626
    style PLATFORM fill:#f0fdf4,stroke:#15803d
    style APIGW fill:#f0f9ff,stroke:#0284c7
```

---

## Diagram 2: Standard (SCORCH) Cluster Provisioning

```mermaid
sequenceDiagram
    autonumber
    participant User as User / Console
    participant APIGW as API Gateway + WAF
    participant Auth as Authorizer Lambda<br/>global-compute-stack-env-authorizer
    participant PING as Ping Identity
    participant CIDS as CIDS RBAC
    participant GC as API Lambda<br/>global-compute-stack-env-api
    participant META as Metadata Service Lambda<br/>accp-metadata-service-env-service
    participant DDB as DynamoDB<br/>cluster-provisioning-env
    participant SCORCH as SCOrch Gateway
    participant PROV as Provision Container<br/>avcd-vce-engine-provision
    participant CFN as CloudFormation
    participant ENGINE as VCE DB Engine

    Note over User,ENGINE: PHASE 1 — Authentication and Authorization
    User->>APIGW: POST /clusters {site_id, config_id}<br/>Header: Authorization: Bearer {jwt}
    APIGW->>Auth: Forward token for validation
    Auth->>PING: GET /pf/JWKS (fetch signing keys)
    PING-->>Auth: JWKS response (RSA public keys)
    Auth->>Auth: Verify JWT signature, expiry, claims
    Auth-->>APIGW: IAM Policy {Allow, principalId, context}
    Note right of Auth: Log: correlation_id, request_id,<br/>layer=authorizer, issuer, claims

    Note over User,ENGINE: PHASE 2 — Request Processing
    APIGW->>GC: Proxy request with auth context
    GC->>GC: Extract correlation_id from X-Correlation-ID header
    GC->>CIDS: POST /v1/rbac/resolve {token, resource, action}
    CIDS-->>GC: {allowed: true, roles: [...]}
    Note right of GC: Log: correlation_id, layer=service,<br/>site_id, config_id, provisioner

    Note over User,ENGINE: PHASE 3 — Config Validation
    GC->>META: GET /v1/compute-engine-configs/{config_id}<br/>Header: X-Correlation-ID
    META->>META: Query DDB compute-engine-configs table
    META-->>GC: {state, site_id, provisioner: SCORCH, org_name}
    GC->>GC: Validate state allows provisioning

    Note over User,ENGINE: PHASE 4 — State Transition and SCOrch Dispatch
    GC->>META: PATCH /v1/compute-engine-configs/{config_id}<br/>{state: PROVISIONING}
    META-->>GC: 200 OK (state updated)
    GC->>SCORCH: POST /scorch/v2/components via Site Gateway<br/>{manifest, params}
    SCORCH-->>GC: {component_id: comp-xxxx}
    GC->>DDB: PutItem {site_id, config_id,<br/>component_id, status: PROVISIONING,<br/>provisioner: SCORCH, created_at}
    Note right of DDB: Log: correlation_id, layer=util,<br/>dynamodb_table, site_id, config_id
    GC-->>User: 202 Accepted {cluster_id, status: PROVISIONING}

    Note over SCORCH,ENGINE: PHASE 5 — Infrastructure Deployment (async)
    SCORCH->>PROV: Launch Docker container<br/>COMPONENT_JOB_TYPE=CREATE
    PROV->>PROV: Phase 1/5: setup (AMI, IAM, S3, subnet)
    PROV->>CFN: Phase 2/5: deploy CloudFormation stack
    CFN-->>PROV: Stack CREATE_COMPLETE
    PROV->>ENGINE: Phase 3/5: poll GET :22222/provisioning-status
    ENGINE-->>PROV: {state: READY}
    PROV->>PROV: Phase 4-5: telemetry + viewpoint

    Note over GC,ENGINE: PHASE 6 — Status Monitor (every 3 min)
    loop Status Monitor Lambda every 3 minutes
        GC->>DDB: Scan non-terminal clusters
        DDB-->>GC: [{site_id, config_id, component_id}]
        GC->>SCORCH: GET /scorch/v2/components/{component_id}/status
        SCORCH-->>GC: {status: RUNNING}
        GC->>DDB: UpdateItem status = RUNNING
        GC->>META: PATCH state = RUNNING
        Note right of GC: Log: service=status-monitor,<br/>component_id, status=RUNNING
    end
```

---

## Diagram 3: Pooled Cluster Provisioning

```mermaid
sequenceDiagram
    autonumber
    participant User as User / Console
    participant GC as API Lambda<br/>global-compute-stack-env-api
    participant META as Metadata Service<br/>accp-metadata-service-env-service
    participant POOL_API as Pooling API Lambda<br/>stackName-api-lambda
    participant POOL_DDB as Pooling DynamoDB<br/>stackName-pool-table
    participant SQS_JOBS as SQS cluster-jobs + DLQ
    participant SQS_OPS as SQS cluster-operations + DLQ
    participant JOB as Job Lambda<br/>stackName-job-lambda
    participant WORKER as Worker Lambda<br/>stackName-worker-lambda
    participant EC2 as AWS EC2 + ASG
    participant GC_DDB as DynamoDB<br/>cluster-provisioning-env
    participant STATUS as Status Monitor Lambda<br/>global-compute-stack-env-status-monitor

    Note over User,STATUS: PHASE 1 — Request and Delegation
    User->>GC: POST /clusters {config_id, provisioner: POOLED}
    GC->>META: GET /v1/compute-engine-configs/{config_id}
    META-->>GC: {provisioner: POOLED, state, site_id}
    GC->>META: PATCH state = PROVISIONING
    GC->>POOL_API: POST /clusters {instanceType, nodeCount}<br/>IAM Auth (Sigv4 signed)
    Note right of GC: Log: correlation_id, provisioner=POOLED

    Note over POOL_API,EC2: PHASE 2 — Pooling Queues the Job
    POOL_API->>POOL_DDB: CreateItem {cluster_id, status: CREATING}
    POOL_API->>SQS_JOBS: SendMessage {jobType: CREATE_CLUSTER,<br/>clusterID, retryCount: 0, maxRetries: 3}
    POOL_API-->>GC: 202 Accepted {cluster_id}
    GC->>GC_DDB: PutItem {site_id, config_id, status: PROVISIONING}
    GC-->>User: 202 Accepted

    Note over SQS_JOBS,EC2: PHASE 3 — Job Lambda Creates Cluster
    SQS_JOBS->>JOB: Lambda trigger (batch size: 1)
    JOB->>POOL_DDB: FindAvailablePool(instanceType)
    alt Pool exists with capacity
        POOL_DDB-->>JOB: {pool_id, asg_name}
    else No pool available
        JOB->>EC2: CreateAutoScalingGroup + LaunchTemplate
        EC2-->>JOB: {asg_name}
        JOB->>POOL_DDB: SavePool {pool_id, asg_name}
    end
    JOB->>EC2: ScalePool — set DesiredCapacity
    JOB->>EC2: WaitForInstances — poll until Running
    EC2-->>JOB: Instances ready

    Note over SQS_OPS,EC2: PHASE 4 — Worker Sets Up Cluster
    JOB->>SQS_OPS: SendMessage {operationType: SETUP, clusterID}
    SQS_OPS->>WORKER: Lambda trigger (batch size: 1)
    WORKER->>WORKER: SetupCluster on each node via HTTP :8080
    WORKER->>POOL_DDB: UpdateItem status = ACTIVE

    Note over GC,STATUS: PHASE 5 — Status Monitor Detects Running
    loop Every 3 minutes
        STATUS->>GC_DDB: Scan PROVISIONING + provisioner=POOLED
        STATUS->>POOL_API: GET /clusters/{cluster_id}
        POOL_API-->>STATUS: {status: ACTIVE}
        STATUS->>GC_DDB: UpdateItem status = RUNNING
        STATUS->>META: PATCH state = RUNNING
    end

    Note over SQS_JOBS,SQS_OPS: ERROR: DLQ after 3 retries
```

---

## Diagram 4: Network and PrivateLink Setup

```mermaid
sequenceDiagram
    autonumber
    participant META as Metadata Service<br/>accp-metadata-service-env-service
    participant NET as Network Service<br/>accp-network-service :8080
    participant EC2 as AWS EC2 API<br/>VPC/Subnet/SG/NAT
    participant SITEGW as Site Gateway
    participant DNS as DNS Service
    participant SNOW as ServiceNow
    participant NET_DDB as DynamoDB<br/>network-svc-sites + status
    participant GC as Global Compute API Lambda
    participant PL_MON as PrivateLink Monitor<br/>global-compute-stack-env-privatelink-monitor
    participant WL as Whitelist-IP Lambda<br/>global-compute-stack-env-whitelist-ip
    participant META_DDB as DynamoDB<br/>vce-sites + configs

    Note over META,META_DDB: FLOW A: VPC Network Deployment (Org Onboarding)

    META->>NET: POST /v1/networks<br/>{vce_site_id, csp: AWS, region, org_name}
    NET->>NET_DDB: Create status record {requestId, IN_PROGRESS}
    Note right of NET: Log: site_id, operation=CREATE, request_id

    rect rgb(230, 245, 255)
        Note over NET,EC2: VPC Infrastructure Creation
        NET->>EC2: CreateVpc {cidr: 10.0.0.0/16}
        EC2-->>NET: {vpc_id}
        NET->>EC2: CreateSubnets (public + private per AZ)
        EC2-->>NET: {subnet_ids}
        NET->>EC2: CreateNatGateway + ElasticIP
        NET->>EC2: CreateSecurityGroups
        NET->>EC2: CreateRouteTables + associations
    end

    NET->>SITEGW: RegisterIngress {vpc_id, subnet_ids}<br/>Poll every 10s, timeout 5min
    SITEGW-->>NET: Ingress registered
    NET->>DNS: POST /dns/records {type: TXT}
    DNS-->>NET: DNS record created
    NET->>SNOW: Deploy Valtix access controller
    NET->>NET_DDB: Update sites + status COMPLETED
    NET->>META: POST /v1/sites/networks (callback)<br/>{vce_site_id, network_status: READY}
    META->>META_DDB: Update vce-sites with network_details

    Note over GC,META_DDB: FLOW B: PrivateLink Creation (per CE)

    GC->>PL_MON: Lambda.Invoke (async)<br/>{site_id, config_id, endpoint_service_name}

    rect rgb(255, 245, 230)
        Note over PL_MON,WL: Self-Invoking Monitor Loop
        loop Poll every 30 seconds via self-invocation
            PL_MON->>EC2: CreateVpcEndpoint or DescribeVpcEndpoints
            EC2-->>PL_MON: {state: pending / available / failed}
            alt State = pending
                PL_MON->>PL_MON: Lambda.Invoke(self) recursive
            else State = available
                PL_MON->>WL: Lambda.Invoke {endpoint_ips, sg_id}
                WL->>EC2: AuthorizeSecurityGroupIngress
                PL_MON->>META: PATCH privatelink_status: ACTIVE
            else State = failed
                PL_MON->>META: PATCH privatelink_status = FAILED
            end
        end
    end

    Note over NET,NET_DDB: FLOW C: Check Deployment Status
    GC->>NET: GET /v1/networks/status/{request_id}
    NET->>NET_DDB: Query by requestId
    NET-->>GC: {status: COMPLETED/IN_PROGRESS/FAILED}
```

---

## Diagram 5: Auto-Suspend and Inactivity Detection

```mermaid
sequenceDiagram
    autonumber
    participant ENGINE as VCE Database Engine
    participant WES as Event Scheduler<br/>workspaces-event-scheduler<br/>gRPC:50051 REST:50061
    participant WSVC as Workspaces Service<br/>localhost:3282 gRPC
    participant SQS as SQS FIFO<br/>auto-suspend-queue-env
    participant SCHED as Scheduler Fargate<br/>cog-global-compute
    participant GC_DDB as DynamoDB<br/>cluster-provisioning-env
    participant GC as Global Compute API
    participant SCORCH as SCOrch Gateway
    participant META as Metadata Service

    Note over ENGINE,META: PATH A: On-Engine Inactivity Detection

    rect rgb(255, 240, 240)
        loop Scheduler poll interval (PollInterval config)
            WES->>ENGINE: SELECT COUNT(*) FROM DBC.SESSIONINFO<br/>WHERE USERNAME IN (enrolled_users)
            ENGINE-->>WES: active_sessions_count

            alt Active sessions > 0
                WES->>WES: Mark project ACTIVE, skip
            else No active sessions
                WES->>ENGINE: FLUSH QUERY LOGGING WITH ALLDBQL
                WES->>ENGINE: SELECT MAX(CollectTimeStamp)<br/>FROM inactivemon.qrylog_ts
                ENGINE-->>WES: last_query_timestamp
                WES->>WES: idle_duration = now - last_query

                alt idle_duration > EventIntervalMinutes
                    WES->>WSVC: gRPC EngineSuspend({ProjectId})<br/>Timeout: 600s
                    WSVC-->>WES: SuspendResponse
                else idle_duration within threshold
                    WES->>WES: Still active
                end
            end
        end
    end

    Note over SQS,META: PATH B: SQS-Driven Auto-Suspend

    rect rgb(240, 240, 255)
        SQS->>SCHED: ReceiveMessage (long-poll 5s)<br/>WORKER_MODE=SQS_PROCESSOR
        SCHED->>GC_DDB: GetItem {site_id, config_id}
        GC_DDB-->>SCHED: {status, provisioner, start_time, end_time}

        alt Cluster RUNNING and within suspend window
            SCHED->>GC: Trigger cluster suspend
            GC->>SCORCH: POST /scorch/v2/components/{id}/stop
            GC->>GC_DDB: UpdateItem status = SUSPENDING
            GC->>META: PATCH state = SUSPENDING
        else Not in suspendable state
            SCHED->>SCHED: Skip
        end
        SCHED->>SQS: DeleteMessage
    end

    Note over SCHED,META: PATH C: DynamoDB Poll Mode
    rect rgb(240, 255, 240)
        loop Every 10 minutes (WORKER_MODE=DynamoDB)
            SCHED->>GC_DDB: Scan clusters with cron expressions
            GC_DDB-->>SCHED: Clusters approaching suspend window
            alt Time to suspend
                SCHED->>GC: Trigger suspend
            end
        end
    end

    Note over GC,META: STATUS CONVERGENCE
    loop Status Monitor every 3 min
        GC->>SCORCH: GET component status
        SCORCH-->>GC: STOPPED
        GC->>GC_DDB: UpdateItem status = SUSPENDED
        GC->>META: PATCH state = SUSPENDED
    end
```

---

## Diagram 6: LMO Lifecycle Orchestrator Workflows

```mermaid
sequenceDiagram
    autonumber
    participant GC as Global Compute API<br/>global-compute-stack-env-api
    participant LMO_API as LMO API<br/>svc-vce-lmo Granian :8000
    participant SQS as SQS FIFO Celery Broker
    participant WORKER as Celery Workers<br/>ECS Fargate
    participant REDIS as ElastiCache Redis<br/>Results + RedBeat
    participant SCORCH as SCOrch Gateway
    participant SITEGW as Site Gateway
    participant NET as Network Service
    participant DNS as DNS Service
    participant OMS as OMS
    participant META as Metadata Service
    participant SECRET as AWS Secrets Manager

    Note over GC,SECRET: FLOW 1: CE Post-Provisioning (ce-post-provisioning-aws)

    GC->>LMO_API: POST /lmo/v1/flows/ce-post-provisioning-aws<br/>Headers: X-LMO-Callback-URL, X-LMO-Callback-Secret
    LMO_API->>SQS: Celery send_task via SQS FIFO
    LMO_API-->>GC: 202 Accepted {run_id}

    SQS->>WORKER: Celery worker consumes task
    WORKER->>REDIS: Store run state {run_id, RUNNING}
    WORKER->>SCORCH: Create manifest from template
    WORKER->>SITEGW: Create app route for CE
    WORKER->>NET: Configure network resources
    WORKER->>DNS: Create DNS records
    WORKER->>REDIS: Update {status: COMPLETED}
    WORKER-->>GC: HTTP POST callback<br/>{run_id, status: COMPLETED}

    Note over GC,SECRET: FLOW 2: OMS Create (oms-create-v1)

    GC->>LMO_API: POST /lmo/v1/flows/oms-create-v1
    LMO_API->>SQS: Queue Celery task
    LMO_API-->>GC: 202 {run_id}
    SQS->>WORKER: Dispatch
    WORKER->>SCORCH: Create OMS component
    WORKER->>SITEGW: Register app route for OMS
    loop Wait for OMS RUNNING
        WORKER->>SCORCH: GET /components/{id}/status
        SCORCH-->>WORKER: PROVISIONING then RUNNING
    end
    WORKER-->>META: Callback oms_status=REGISTERED

    Note over GC,SECRET: FLOW 3: Secret Deletion (delete-secret-aws)

    GC->>LMO_API: POST /lmo/v1/flows/delete-secret-aws
    LMO_API->>SQS: Queue task
    LMO_API-->>GC: 202 {run_id}
    SQS->>WORKER: Dispatch
    WORKER->>SECRET: DeleteSecret
    WORKER-->>GC: Callback COMPLETED

    Note over GC,SECRET: FLOW 4: Secret Rotation

    GC->>LMO_API: POST /lmo/v1/flows/randomize-secret-value-aws
    LMO_API->>SQS: Queue task
    SQS->>WORKER: Dispatch
    WORKER->>SECRET: GetSecretValue then PutSecretValue
    WORKER-->>GC: Callback COMPLETED

    Note over LMO_API,REDIS: MONITORING
    GC->>LMO_API: GET /lmo/v1/runs/{run_id}
    LMO_API->>REDIS: Get run info
    LMO_API-->>GC: {run_id, status, result}
```

---

## Diagram 7: VCE Engine Image Build and Deploy Pipeline

```mermaid
graph LR
    subgraph BOM_STAGE["Stage 1: Bill of Materials"]
        BOM_DEV["specs/dev.yaml<br/>Development BOM"]
        BOM_REL["specs/release.yaml<br/>Release BOM"]
        ODIN["Odin CLI<br/>Package Resolver"]
        ARTIF["Artifactory<br/>secondary artifactory domain"]
    end

    subgraph CONFIG_STAGE["Stage 2: Salt Configuration"]
        SALT_PRE["Pre-init Salt States<br/>Run during image bake"]
        SALT_POST["Post-init Salt States<br/>Packaged as RPMs"]
        RPM_COMMON["vce-engine-configure-common.rpm"]
        RPM_AWS["vce-engine-configure-aws.rpm"]
        RPM_AZURE["vce-engine-configure-azure.rpm"]
        RPM_GCP["vce-engine-configure-gcp.rpm"]
    end

    subgraph PACKER_STAGE["Stage 3: Image Building"]
        PACKER_HCL["engine.pkr.hcl<br/>Multi-cloud Packer"]
        subgraph PROV_STEPS["Provisioning Steps"]
            P1["1. Install pre-RPMs<br/>Datadog Qualys Prisma"]
            P2["2. Cloud metadata packages"]
            P3["3. Disk partitions"]
            P4["4. salt-call state.db.initialize<br/>300min timeout"]
            P5["5. Docker + OTF + UDF"]
            P6["6. Security agents"]
            P7["7. Cleanup + finalize"]
        end
        AMI["AWS AMI"]
        AZURE_IMG["Azure Managed Image"]
        GCP_IMG["GCP Image"]
    end

    subgraph DEPLOY_STAGE["Stage 4: Provision via Scorch"]
        RUNNER["runner.sh"]
        subgraph CREATE_PHASES["CREATE Phases"]
            D1["Phase 1: setup<br/>AMI IAM S3 subnet"]
            D2["Phase 2: deploy<br/>CloudFormation"]
            D3["Phase 3: configure<br/>Poll :22222 health"]
            D4["Phase 4: telemetry"]
            D5["Phase 5: viewpoint"]
        end
        CFN["CloudFormation Stack"]
        LAMBDA_ROT["Password Rotation Lambda"]
    end

    subgraph ENGINE_STAGE["Stage 5: Running Engine"]
        ENGINE["VCE Database Engine"]
        WES["Event Scheduler<br/>gRPC:50051 REST:50061"]
    end

    BOM_DEV --> ODIN
    BOM_REL --> ODIN
    ODIN -->|"Fetch packages"| ARTIF
    ARTIF -->|"RPMs"| PACKER_HCL

    SALT_PRE --> RPM_COMMON
    SALT_POST --> RPM_COMMON
    SALT_PRE --> RPM_AWS
    SALT_PRE --> RPM_AZURE
    SALT_PRE --> RPM_GCP
    RPM_COMMON --> PACKER_HCL
    RPM_AWS --> PACKER_HCL

    PACKER_HCL --> P1 --> P2 --> P3 --> P4 --> P5 --> P6 --> P7
    P7 -->|"AWS"| AMI
    P7 -->|"Azure"| AZURE_IMG
    P7 -->|"GCP"| GCP_IMG

    AMI --> RUNNER
    RUNNER --> D1 --> D2 --> D3 --> D4 --> D5
    D2 --> CFN
    D2 --> LAMBDA_ROT
    CFN --> ENGINE
    D5 --> ENGINE
    ENGINE --- WES

    style BOM_STAGE fill:#fef3c7,stroke:#d97706
    style CONFIG_STAGE fill:#dcfce7,stroke:#16a34a
    style PACKER_STAGE fill:#dbeafe,stroke:#2563eb
    style DEPLOY_STAGE fill:#fce7f3,stroke:#db2777
    style ENGINE_STAGE fill:#fee2e2,stroke:#dc2626
```

---

## Diagram 8: Data Store and Queue Map

```mermaid
graph TB
    subgraph GC_DATA["cog-global-compute Data"]
        GC_DDB["DynamoDB: cluster-provisioning-env<br/>PK: site_id | SK: config_id<br/>GSI: ComponentIndex on component_id<br/>Fields: status, provisioner, component_id,<br/>start_time, end_time, qg_status, oms_status"]
        GC_SQS["SQS FIFO: auto-suspend-queue-env<br/>Content dedup enabled<br/>DLQ: auto-suspend-dlq-env (max 3)"]
        GC_S3["S3: Manifest bucket<br/>Cluster manifests"]
    end

    subgraph META_DATA["accp-metadata-service Data"]
        M1["DynamoDB: compute-engine-configs<br/>PK: compute_engine_config_id<br/>GSIs: erp, orgname, site-id, issuer, orgname-name"]
        M2["DynamoDB: organizations<br/>PK: id<br/>GSIs: name-index, erp-index"]
        M3["DynamoDB: vce-sites<br/>PK: site_id<br/>GSIs: erp-site-id, org-name-site-id"]
        M4["DynamoDB: ce-infrastructure<br/>PK: compute_engine_config_id<br/>Fields: vpc_id, subnet_ids, node_ips, tdbms_version"]
        M5["DynamoDB: ce-site-id-counter<br/>PK: org_name<br/>Atomic counter for 17-char CE Site IDs"]
    end

    subgraph NET_DATA["accp-network-service Data"]
        N1["DynamoDB: network-svc-sites<br/>PK: siteId<br/>On-demand billing, deletion protection"]
        N2["DynamoDB: network-svc-status<br/>PK: requestId<br/>TTL: expirationTime (4 weeks)"]
    end

    subgraph LMO_DATA["svc-vce-lmo Data"]
        L1["SQS FIFO: Celery Broker<br/>4-day retention, 20s long-poll<br/>256KB max message, KMS encrypted"]
        L2["ElastiCache Serverless: Redis/Valkey 7<br/>Task results + RedBeat scheduler<br/>5GB max, 10K ECPU, KMS, RBAC"]
    end

    subgraph POOL_DATA["pooling-service Data"]
        P1["DynamoDB: pool-table<br/>GSI on region<br/>Pools, clusters, nodes, schedules"]
        P2["SQS: cluster-operations + DLQ<br/>Visibility: 16min, max retries: 3"]
        P3["SQS: cluster-jobs + DLQ<br/>Visibility: 16min, max retries: 3"]
        P4["SSM Parameter: /stackName/infra-config<br/>JSON with VPC, subnets, SGs, queue URLs"]
    end

    subgraph ENGINE_DATA["On-Engine Data"]
        E1["SQLite: workspaces.db<br/>projects, collaborators, tasks, events"]
        E2["Teradata DB: inactivemon schema<br/>qrylog_ts, init_time tables"]
        E3["Log files: sched_logs/sched.log<br/>zerolog JSON, lumberjack rotation"]
    end

    style GC_DATA fill:#dbeafe,stroke:#2563eb
    style META_DATA fill:#dcfce7,stroke:#16a34a
    style NET_DATA fill:#fce7f3,stroke:#db2777
    style LMO_DATA fill:#f3e8ff,stroke:#9333ea
    style POOL_DATA fill:#ffedd5,stroke:#ea580c
    style ENGINE_DATA fill:#fee2e2,stroke:#dc2626
```
