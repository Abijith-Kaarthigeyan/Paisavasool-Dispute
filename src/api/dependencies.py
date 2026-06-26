from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config.settings import settings
from src.core.services.assignment_service import AssignmentService
from src.core.services.associate_communication_service import (
    AssociateCommunicationService,
)
from src.core.services.audit_service import AuditService
from src.core.services.correlation_service import CorrelationService
from src.core.services.escalation_service import EscalationService
from src.core.services.recommendation_service import RecommendationService
from src.core.services.sla_service import SLAService
from src.core.services.validation_service import ValidationService
from src.core.services.workflow_context_service import WorkflowContextService
from src.core.workflow.evidence_service import EvidenceSnapshotService
from src.core.workflow.interrupt_service import WorkflowInterruptService
from src.core.workflow.resume_service import DisputeResumeService
from src.data.clients.ar_service_client import ARServiceClient
from src.data.clients.postgres_client import get_async_db
from src.data.repositories import (
    ActivityRepository,
    AgentRunRepository,
    AssignmentRepository,
    CaseRepository,
    CommentRepository,
    CommunicationRepository,
    DisputeRepository,
    EscalationRepository,
    EvidenceSnapshotRepository,
    RecommendationRepository,
    ReviewQueueRepository,
    SLARepository,
    UserRepository,
    WorkflowContextRepository,
)

# Repository Dependency Getters


def get_case_repository(db: AsyncSession = Depends(get_async_db)) -> CaseRepository:
    return CaseRepository(db)


def get_dispute_repository(
    db: AsyncSession = Depends(get_async_db),
) -> DisputeRepository:
    return DisputeRepository(db)


def get_assignment_repository(
    db: AsyncSession = Depends(get_async_db),
) -> AssignmentRepository:
    return AssignmentRepository(db)


def get_sla_repository(db: AsyncSession = Depends(get_async_db)) -> SLARepository:
    return SLARepository(db)


def get_escalation_repository(
    db: AsyncSession = Depends(get_async_db),
) -> EscalationRepository:
    return EscalationRepository(db)


def get_workflow_context_repository(
    db: AsyncSession = Depends(get_async_db),
) -> WorkflowContextRepository:
    return WorkflowContextRepository(db)


def get_recommendation_repository(
    db: AsyncSession = Depends(get_async_db),
) -> RecommendationRepository:
    return RecommendationRepository(db)


def get_review_queue_repository(
    db: AsyncSession = Depends(get_async_db),
) -> ReviewQueueRepository:
    return ReviewQueueRepository(db)


def get_comment_repository(
    db: AsyncSession = Depends(get_async_db),
) -> CommentRepository:
    return CommentRepository(db)


def get_activity_repository(
    db: AsyncSession = Depends(get_async_db),
) -> ActivityRepository:
    return ActivityRepository(db)


def get_user_repository(db: AsyncSession = Depends(get_async_db)) -> UserRepository:
    return UserRepository(db)


def get_communication_repository(
    db: AsyncSession = Depends(get_async_db),
) -> CommunicationRepository:
    return CommunicationRepository(db)


# Client Getters


def get_ar_service_client() -> ARServiceClient:
    return ARServiceClient()


# Service Dependency Getters


def get_audit_service(
    activity_repo: ActivityRepository = Depends(get_activity_repository),
) -> AuditService:
    return AuditService(activity_repo)


def get_assignment_service(
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
    assignment_repo: AssignmentRepository = Depends(get_assignment_repository),
    user_repo: UserRepository = Depends(get_user_repository),
    audit_service: AuditService = Depends(get_audit_service),
) -> AssignmentService:
    return AssignmentService(dispute_repo, assignment_repo, user_repo, audit_service)


def get_sla_service(
    sla_repo: SLARepository = Depends(get_sla_repository),
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
    audit_service: AuditService = Depends(get_audit_service),
) -> SLAService:
    return SLAService(sla_repo, dispute_repo, audit_service, settings)


def get_escalation_service(
    escalation_repo: EscalationRepository = Depends(get_escalation_repository),
    sla_repo: SLARepository = Depends(get_sla_repository),
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
    audit_service: AuditService = Depends(get_audit_service),
) -> EscalationService:
    return EscalationService(escalation_repo, sla_repo, dispute_repo, audit_service)


def get_correlation_service(
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
    communication_repo: CommunicationRepository = Depends(get_communication_repository),
    comment_repo: CommentRepository = Depends(get_comment_repository),
    case_repo: CaseRepository = Depends(get_case_repository),
    audit_service: AuditService = Depends(get_audit_service),
) -> CorrelationService:
    return CorrelationService(
        dispute_repo,
        communication_repo,
        comment_repo,
        audit_service,
        case_repo,
    )


def get_workflow_context_service(
    context_repo: WorkflowContextRepository = Depends(get_workflow_context_repository),
    audit_service: AuditService = Depends(get_audit_service),
) -> WorkflowContextService:
    return WorkflowContextService(context_repo, audit_service)


def get_recommendation_service(
    rec_repo: RecommendationRepository = Depends(get_recommendation_repository),
    audit_service: AuditService = Depends(get_audit_service),
) -> RecommendationService:
    return RecommendationService(rec_repo, audit_service)


def get_validation_service(
    ar_client: ARServiceClient = Depends(get_ar_service_client),
) -> ValidationService:
    return ValidationService(ar_client)


def get_agent_run_repository(
    db: AsyncSession = Depends(get_async_db),
) -> AgentRunRepository:
    return AgentRunRepository(db)


def get_evidence_snapshot_repository(
    db: AsyncSession = Depends(get_async_db),
) -> EvidenceSnapshotRepository:
    return EvidenceSnapshotRepository(db)


def get_resume_service() -> DisputeResumeService:
    return DisputeResumeService()


def get_interrupt_service(
    context_repo: WorkflowContextRepository = Depends(get_workflow_context_repository),
    audit_service: AuditService = Depends(get_audit_service),
) -> WorkflowInterruptService:
    return WorkflowInterruptService(context_repo, audit_service)


def get_evidence_snapshot_service(
    snapshot_repo: EvidenceSnapshotRepository = Depends(
        get_evidence_snapshot_repository
    ),
) -> EvidenceSnapshotService:
    return EvidenceSnapshotService(snapshot_repo)


def get_associate_communication_service(
    comm_repo: CommunicationRepository = Depends(get_communication_repository),
    activity_repo: ActivityRepository = Depends(get_activity_repository),
    comment_repo: CommentRepository = Depends(get_comment_repository),
    context_repo: WorkflowContextRepository = Depends(get_workflow_context_repository),
    audit_service: AuditService = Depends(get_audit_service),
) -> AssociateCommunicationService:
    return AssociateCommunicationService(
        comm_repo=comm_repo,
        activity_repo=activity_repo,
        comment_repo=comment_repo,
        context_repo=context_repo,
        audit_service=audit_service,
    )
