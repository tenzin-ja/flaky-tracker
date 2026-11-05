---
status: Proposed
date: 2026-10-30
source: RFC 0001 rev. 5 D7, D9, D16, T1, T7, §5.1
supersedes:
superseded_by:
---

# 0010. Deploy to AWS ECS Fargate with Terraform

## Context

The beta needs an always-available HTTPS endpoint for webhooks, a worker, a
database and a broker. The deployment itself is part of the project's goals:
infrastructure as code, containers and CI/CD without stored cloud keys.

## Constraints

- Low traffic; a small monthly budget.
- New to Terraform and AWS networking; deploy work is planned after the local
  end-to-end demo.
- Must be reproducible from documented steps and torn down when idle.

## Alternatives considered

| Option | For | Against |
| --- | --- | --- |
| **ECS Fargate + Terraform** | Containers without servers; common in industry; teaches IAM, networking and IaC | Fixed costs (load balancer, database, broker); steep first setup |
| A platform host (Render, Fly.io, Railway) | Fastest and cheapest to run | Little infrastructure learning |
| EC2 with Docker Compose | Cheap; one machine | Manual patching and deploys |
| EKS | Industry-relevant | Far more than the beta needs |
| Lambda | Pay per use | Doesn't fit long-running Celery workers |

## Decision

Terraform defines: ECR; ECS Fargate services for web and one worker (the
worker deploys stop-before-start); an EventBridge Scheduler schedule for the
recovery task ([0005](0005-recorded-run-recovery.md)); RDS PostgreSQL; a Redis
broker; an ALB with an ACM certificate; Secrets Manager; CloudWatch logs and
essential alarms. GitHub Actions deploys through OIDC. Sentry, tracing,
autoscaling and multi-AZ are deferred.

## Consequences

- A monthly cost even with no users; needs a budget alert and a teardown plan.
- The Redis option (managed or container) and network layout are still open
  (RFC §11).
- Deploys must run migrations before updating services.

## Revisit if

- Costs exceed the cap.
- Deployment work threatens the beta date (consider a platform host first and
  AWS after).
