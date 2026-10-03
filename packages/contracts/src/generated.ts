/* eslint-disable */
// GENERATED from packages/contracts/openapi.json by scripts/generate.mjs.
// Do not edit by hand: change the Pydantic models, then run `pnpm contracts:generate`.

export interface paths {
    "/actions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Actions
         * @description Analysts may read the queue; only supervisors may decide on it.
         */
        get: operations["list_actions_actions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/actions/{action_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Action */
        get: operations["get_action_actions__action_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/actions/{action_id}/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Approve
         * @description One transaction: re-read FOR UPDATE, evaluate ``domain.approval``, write the
         *     approval row, flip the status, insert the outbox event. Commit.
         *
         *     The Celery message is published by the dispatcher from the outbox — never from
         *     inside this request (ADR 0003).
         */
        post: operations["approve_actions__action_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/actions/{action_id}/reject": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reject
         * @description Rejection is terminal and produces no outbox event and no downstream effect.
         */
        post: operations["reject_actions__action_id__reject_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/dev/token": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Issue Dev Token
         * @description Mint a token for a seeded membership.
         *
         *     It grants nothing an administrator has not already granted: the subject, role and
         *     organization all come from an existing ``memberships`` row. With no such row there
         *     is no token — and the refusal is a 404, matching how the rest of the API declines
         *     to confirm what exists.
         */
        post: operations["issue_dev_token_auth_dev_token_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/documents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Documents */
        get: operations["list_documents_documents_get"];
        put?: never;
        /**
         * Upload Document
         * @description Validate multipart metadata, then read at most the limit plus one byte.
         *
         *     Hash the normalized content, create or reuse a version, then enqueue indexing.
         *
         *     An identical ``content_hash`` is a no-op that returns the existing version — that
         *     is what makes a retried upload safe.
         */
        post: operations["upload_document_documents_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/documents/versions/{version_id}/chunks/{ordinal}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Read Citation */
        get: operations["read_citation_documents_versions__version_id__chunks__ordinal__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/documents/{document_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * Delete Document
         * @description Removes the document's chunks in the same transaction that marks it deleted.
         *
         *     A deleted policy must be invisible to the very next retrieval. Tenant- and
         *     deletion-visibility tests are a Week 3 gate.
         */
        delete: operations["delete_document_documents__document_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/documents/{document_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry Index */
        post: operations["retry_index_documents__document_id__retry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Me */
        get: operations["me_me_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/metrics/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Summary
         * @description Analysts and supervisors see their own organization's numbers, nobody else's.
         */
        get: operations["summary_metrics_summary_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ops/tasks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Lookup Task
         * @description The reconciler's question: did this key ever produce a task?
         *
         *     A definitive ``null`` means it is safe to retry with the same key. An error here is
         *     *not* a null — the caller must keep the action in ``reconciliation_required``.
         */
        get: operations["lookup_task_ops_tasks_get"];
        put?: never;
        /**
         * Create Task
         * @description Insert with ``ON CONFLICT (idempotency_key) DO NOTHING``, then read the row back.
         *
         *     Doing it in that order is what makes two concurrent deliveries safe: the loser of
         *     the race reads the winner's row and returns the same ``task_id`` with 200. Never
         *     check-then-insert — the gap between the two is exactly where the duplicate is born.
         */
        post: operations["create_task_ops_tasks_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/orders": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Orders
         * @description ``flagged_only`` keeps orders a carrier reported delayed or in exception;
         *     ``q`` matches order number or customer name.
         */
        get: operations["list_orders_orders_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/orders/counts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Order Counts */
        get: operations["order_counts_orders_counts_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/orders/{order_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Order
         * @description A user in organization A requesting B's order id gets 404, never 403.
         *
         *     This is the Week 1 release gate. The repository raises ``ResourceNotFound`` for a
         *     row outside the principal's organization, so the disclosure cannot happen here even
         *     if a future edit forgets about it — and the composite foreign keys mean it cannot
         *     happen in the database either.
         */
        get: operations["get_order_orders__order_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Runs */
        get: operations["list_runs_runs_get"];
        put?: never;
        /**
         * Create Run
         * @description Reserve budget, persist the run as ``queued``, enqueue after commit.
         */
        post: operations["create_run_runs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run
         * @description A run in another organization is a 404, never a 403.
         */
        get: operations["get_run_runs__run_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Cancel Run
         * @description Cancels a queued run. An in-flight run stops at the orchestrator's next check.
         */
        post: operations["cancel_run_runs__run_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/feedback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Submit Feedback */
        post: operations["submit_feedback_runs__run_id__feedback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/system": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** System Info */
        get: operations["system_info_system_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * ActionStatus
         * @enum {string}
         */
        ActionStatus: "pending" | "approved" | "rejected" | "expired" | "executing" | "succeeded" | "failed" | "reconciliation_required";
        /**
         * ActionType
         * @description The complete set of writes the system can propose. One member, on purpose.
         * @enum {string}
         */
        ActionType: "create_followup_task";
        /** ActionView */
        ActionView: {
            action_type: components["schemas"]["ActionType"];
            /** Assignee Name */
            assignee_name: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Decision Expired */
            decision_expired: boolean;
            /** Downstream Task Id */
            downstream_task_id: string | null;
            /** Execution Error */
            execution_error: string | null;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Payload */
            payload: {
                [key: string]: unknown;
            };
            /** Payload Hash */
            payload_hash: string;
            /** Payload Valid */
            payload_valid: boolean;
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /** Source Changed */
            source_changed: boolean;
            /**
             * Source Record Id
             * Format: uuid
             */
            source_record_id: string;
            /** Source Record Type */
            source_record_type: string;
            /** Source Record Version */
            source_record_version: number;
            status: components["schemas"]["ActionStatus"];
        };
        /** Body_upload_document_documents_post */
        Body_upload_document_documents_post: {
            /** Document Id */
            document_id?: string | null;
            /**
             * Effective From
             * Format: date
             */
            effective_from: string;
            /** File */
            file: string;
            /** Title */
            title: string;
        };
        /** CreateRunRequest */
        CreateRunRequest: {
            /** Order Number */
            order_number?: string | null;
            /** Request Text */
            request_text: string;
        };
        /** CreateTaskRequest */
        CreateTaskRequest: {
            /**
             * Assignee Id
             * Format: uuid
             */
            assignee_id: string;
            /** Description */
            description: string;
            /**
             * Due At
             * Format: date-time
             */
            due_at: string;
            /**
             * Order Id
             * Format: uuid
             */
            order_id: string;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
        };
        /** DecisionRequest */
        DecisionRequest: {
            /** Comment */
            comment?: string | null;
            /** Payload Hash */
            payload_hash: string;
        };
        /** DevTokenRequest */
        DevTokenRequest: {
            /**
             * Email
             * Format: email
             */
            email: string;
        };
        /** DevTokenResponse */
        DevTokenResponse: {
            /** Access Token */
            access_token: string;
            /** Expires In */
            expires_in: number;
            /** Organization Id */
            organization_id: string;
            /** Role */
            role: string;
            /**
             * Token Type
             * @default Bearer
             */
            token_type: string;
        };
        /**
         * DocumentStatus
         * @enum {string}
         */
        DocumentStatus: "pending" | "indexing" | "active" | "failed" | "deleted";
        /** DocumentView */
        DocumentView: {
            /** Active Version */
            active_version: number | null;
            /** Chunk Count */
            chunk_count: number;
            /** Effective From */
            effective_from: string | null;
            /** Embedding Mode */
            embedding_mode?: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Latest Effective From */
            latest_effective_from?: string | null;
            /** Latest Version */
            latest_version?: number | null;
            status: components["schemas"]["DocumentStatus"];
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** FeedbackRequest */
        FeedbackRequest: {
            /** Correction Text */
            correction_text?: string | null;
            /** Failure Class */
            failure_class?: string | null;
            usefulness: components["schemas"]["Usefulness"];
        };
        /** FeedbackView */
        FeedbackView: {
            /** Author */
            author: string;
            /** Correction Text */
            correction_text: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Failure Class */
            failure_class: string | null;
            /** Mine */
            mine: boolean;
            usefulness: components["schemas"]["Usefulness"];
        };
        /**
         * Finding
         * @description One claim, with the evidence that supports it.
         */
        Finding: {
            /** Evidence Ids */
            evidence_ids?: string[];
            /** Kind */
            kind: string;
            /** Statement */
            statement: string;
        };
        /**
         * FollowupTaskView
         * @description A task in the SIMULATED ERP, created only by an approved proposal.
         */
        FollowupTaskView: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Description */
            description: string;
            /**
             * Due At
             * Format: date-time
             */
            due_at: string;
            /** Evidence Id */
            evidence_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Status */
            status: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * IncompleteReason
         * @description Why a run stopped short. A run can be useful and incomplete at the same time.
         * @enum {string}
         */
        IncompleteReason: "budget_exhausted" | "turn_limit" | "tool_limit" | "deadline" | "provider_unavailable" | "insufficient_evidence" | "cancelled";
        /** InvestigationRef */
        InvestigationRef: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Incomplete Reason */
            incomplete_reason: string | null;
            /** Status */
            status: string;
        };
        /** MeResponse */
        MeResponse: {
            /**
             * Business Date
             * Format: date
             */
            business_date: string;
            /** Can Approve */
            can_approve: boolean;
            /** Display Name */
            display_name: string;
            /** Email */
            email: string;
            /**
             * Membership Id
             * Format: uuid
             */
            membership_id: string;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            role: components["schemas"]["Role"];
            /** Subject */
            subject: string;
        };
        /** MetricsSummary */
        MetricsSummary: {
            /** Approval Rate */
            approval_rate: number | null;
            /** By Error Code */
            by_error_code: {
                [key: string]: number;
            };
            /** By Incomplete Reason */
            by_incomplete_reason: {
                [key: string]: number;
            };
            /** By Model */
            by_model: components["schemas"]["ModelBreakdown"][];
            /** Cancelled */
            cancelled: number;
            /** Citation Support Ratio */
            citation_support_ratio: number | null;
            /** Citations Cited */
            citations_cited: number;
            /** Citations Unsupported */
            citations_unsupported: number;
            /** Completed */
            completed: number;
            /** Days */
            days: number;
            /** Executions Failed */
            executions_failed: number;
            /** Executions Succeeded */
            executions_succeeded: number;
            /** Failed */
            failed: number;
            /** Feedback */
            feedback: {
                [key: string]: number;
            };
            /** In Flight */
            in_flight: number;
            /** Incomplete */
            incomplete: number;
            /** Mean Cost Usd */
            mean_cost_usd: number;
            /** Mean Duration Ms */
            mean_duration_ms: number;
            /** Mean Provider Latency Ms */
            mean_provider_latency_ms: number;
            /** Needs Review */
            needs_review: number;
            /** P95 Cost Usd */
            p95_cost_usd: number;
            /** P95 Duration Ms */
            p95_duration_ms: number;
            /** P95 Provider Latency Ms */
            p95_provider_latency_ms: number;
            /** P95 Queue Wait Ms */
            p95_queue_wait_ms: number;
            /** Proposals Approved */
            proposals_approved: number;
            /** Proposals Created */
            proposals_created: number;
            /** Proposals Expired */
            proposals_expired: number;
            /** Proposals Pending */
            proposals_pending: number;
            /** Proposals Rejected */
            proposals_rejected: number;
            /** Reconciliation Required */
            reconciliation_required: number;
            /** Retries */
            retries: number;
            /** Run Count */
            run_count: number;
            /** Runs With Unsupported Citations */
            runs_with_unsupported_citations: number;
            /** Tokens In */
            tokens_in: number;
            /** Tokens Out */
            tokens_out: number;
            /** Tool Calls */
            tool_calls: number;
            /** Tool Errors */
            tool_errors: {
                [key: string]: number;
            };
            /** Tool Outcomes */
            tool_outcomes: {
                [key: string]: components["schemas"]["ToolOutcome"];
            };
            /** Total Cost Usd */
            total_cost_usd: number;
            /** Truncated */
            truncated: boolean;
            /**
             * Window From
             * Format: date
             */
            window_from: string;
            /**
             * Window To
             * Format: date
             */
            window_to: string;
        };
        /**
         * ModelBreakdown
         * @description Results are attributable only to (provider, model, prompt version) together.
         */
        ModelBreakdown: {
            /** Mean Cost Usd */
            mean_cost_usd: number;
            /** Model Name */
            model_name: string;
            /** P95 Duration Ms */
            p95_duration_ms: number;
            /** Prompt Version */
            prompt_version: string;
            /** Provider */
            provider: string;
            /** Runs */
            runs: number;
        };
        /** OrderCounts */
        OrderCounts: {
            /**
             * As Of
             * Format: date
             */
            as_of: string;
            /** Overdue */
            overdue: number;
            /** Total */
            total: number;
        };
        /** OrderDetail */
        OrderDetail: {
            /** Customer Name */
            customer_name: string;
            /** Days Overdue */
            days_overdue: number;
            /** Events */
            events: components["schemas"]["ShipmentEventView"][];
            /** Evidence Id */
            evidence_id: string;
            /** Followup Tasks */
            followup_tasks: components["schemas"]["FollowupTaskView"][];
            /** Has Open Exception */
            has_open_exception: boolean;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Investigations */
            investigations: components["schemas"]["InvestigationRef"][];
            /** Lines */
            lines: components["schemas"]["OrderLineView"][];
            /** Order Number */
            order_number: string;
            /**
             * Promised Date
             * Format: date
             */
            promised_date: string;
            /** Record Version */
            record_version: number;
            /** Shipments */
            shipments: components["schemas"]["ShipmentView"][];
            status: components["schemas"]["OrderStatus"];
            /** Total Amount */
            total_amount: string;
        };
        /** OrderLineView */
        OrderLineView: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Outstanding */
            outstanding: number;
            /** Product Name */
            product_name: string;
            /** Quantity */
            quantity: number;
            /** Quantity Shipped */
            quantity_shipped: number;
            /** Sku */
            sku: string;
            /** Unit Price */
            unit_price: string;
        };
        /**
         * OrderStatus
         * @enum {string}
         */
        OrderStatus: "open" | "partially_shipped" | "shipped" | "delivered" | "cancelled";
        /** OrderSummary */
        OrderSummary: {
            /** Customer Name */
            customer_name: string;
            /** Days Overdue */
            days_overdue: number;
            /** Evidence Id */
            evidence_id: string;
            /** Has Open Exception */
            has_open_exception: boolean;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Order Number */
            order_number: string;
            /**
             * Promised Date
             * Format: date
             */
            promised_date: string;
            status: components["schemas"]["OrderStatus"];
            /** Total Amount */
            total_amount: string;
        };
        /** PolicyCitation */
        PolicyCitation: {
            /** Citation Id */
            citation_id: string;
            /** Relevance */
            relevance: string;
        };
        /**
         * PolicyPassageView
         * @description One cited passage. ``text`` is untrusted document content, rendered as text.
         */
        PolicyPassageView: {
            /**
             * Effective From
             * Format: date
             */
            effective_from: string;
            /** Effective To */
            effective_to: string | null;
            /** Heading Path */
            heading_path: string;
            /** Text */
            text: string;
            /** Title */
            title: string;
            /** Version */
            version: number;
        };
        /**
         * ProposedActionDraft
         * @description What the model may *suggest*. It carries no authority and no execution path.
         *
         *     Note what is absent: no organization id, no approver, no task id, no status. The
         *     backend supplies every one of those. The model cannot address another tenant, and
         *     cannot claim an action happened.
         */
        ProposedActionDraft: {
            /** @default create_followup_task */
            action_type: components["schemas"]["ActionType"];
            /** Description */
            description: string;
            /** Due In Days */
            due_in_days: number;
            /** Rationale */
            rationale: string;
        };
        /**
         * Role
         * @enum {string}
         */
        Role: "analyst" | "supervisor";
        /** RunBounds */
        RunBounds: {
            /** Deadline Seconds */
            deadline_seconds: number;
            /** Max Tool Calls */
            max_tool_calls: number;
            /** Max Turns */
            max_turns: number;
        };
        /** RunDetail */
        RunDetail: {
            /** Can Cancel */
            can_cancel: boolean;
            /** Cancel Requested At */
            cancel_requested_at: string | null;
            /** Cost Usd */
            cost_usd: number;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Duration Ms */
            duration_ms: number | null;
            /** Error Code */
            error_code: string | null;
            /** Feedback */
            feedback: components["schemas"]["FeedbackView"][];
            /** Feedback Ratings */
            feedback_ratings: components["schemas"]["Usefulness"][];
            /** Finished At */
            finished_at: string | null;
            /** Has Proposal */
            has_proposal: boolean;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            incomplete_reason: components["schemas"]["IncompleteReason"] | null;
            /** Initiated By Me */
            initiated_by_me: boolean;
            /** Initiated By Name */
            initiated_by_name: string | null;
            /** Model Name */
            model_name: string;
            /** Order Id */
            order_id: string | null;
            /** Order Number */
            order_number: string | null;
            /** Prompt Version */
            prompt_version: string;
            /** Proposal Id */
            proposal_id: string | null;
            proposal_status: components["schemas"]["ActionStatus"] | null;
            /** Provider */
            provider: string;
            /** Request Text */
            request_text: string;
            result: components["schemas"]["RunResultView"] | null;
            /** Retry Count */
            retry_count: number;
            /** Started At */
            started_at: string | null;
            status: components["schemas"]["RunStatus"];
            /** Steps */
            steps: components["schemas"]["RunStepView"][];
            /** Tokens In */
            tokens_in: number;
            /** Tokens Out */
            tokens_out: number;
            /** Tool Call Count */
            tool_call_count: number;
            /** Trace Pruned At */
            trace_pruned_at: string | null;
            /** Turn Count */
            turn_count: number;
        };
        /**
         * RunResultView
         * @description The validated answer as persisted: the agent's schema plus its accounting.
         */
        RunResultView: {
            /** Findings */
            findings?: components["schemas"]["Finding"][];
            /** Missing Information */
            missing_information?: string[];
            /** Policy Citations */
            policy_citations?: components["schemas"]["PolicyCitation"][];
            proposed_action?: components["schemas"]["ProposedActionDraft"] | null;
            /** Summary */
            summary: string;
            validation?: components["schemas"]["ValidationView"] | null;
        };
        /**
         * RunStatus
         * @enum {string}
         */
        RunStatus: "queued" | "investigating" | "needs_review" | "completed" | "failed" | "cancelled";
        /** RunStepView */
        RunStepView: {
            /** Error Code */
            error_code: string | null;
            /** Latency Ms */
            latency_ms: number;
            /** Ordinal */
            ordinal: number;
            /** Result Row Count */
            result_row_count: number | null;
            /** Step Type */
            step_type: string;
            /** Tokens In */
            tokens_in: number;
            /** Tokens Out */
            tokens_out: number;
            /** Tool Name */
            tool_name: string | null;
        };
        /** RunSummary */
        RunSummary: {
            /** Can Cancel */
            can_cancel: boolean;
            /** Cancel Requested At */
            cancel_requested_at: string | null;
            /** Cost Usd */
            cost_usd: number;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Duration Ms */
            duration_ms: number | null;
            /** Error Code */
            error_code: string | null;
            /** Feedback Ratings */
            feedback_ratings: components["schemas"]["Usefulness"][];
            /** Finished At */
            finished_at: string | null;
            /** Has Proposal */
            has_proposal: boolean;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            incomplete_reason: components["schemas"]["IncompleteReason"] | null;
            /** Initiated By Me */
            initiated_by_me: boolean;
            /** Initiated By Name */
            initiated_by_name: string | null;
            /** Model Name */
            model_name: string;
            /** Order Id */
            order_id: string | null;
            /** Order Number */
            order_number: string | null;
            /** Prompt Version */
            prompt_version: string;
            proposal_status: components["schemas"]["ActionStatus"] | null;
            /** Provider */
            provider: string;
            /** Request Text */
            request_text: string;
            /** Retry Count */
            retry_count: number;
            /** Started At */
            started_at: string | null;
            status: components["schemas"]["RunStatus"];
            /** Tokens In */
            tokens_in: number;
            /** Tokens Out */
            tokens_out: number;
            /** Tool Call Count */
            tool_call_count: number;
            /** Trace Pruned At */
            trace_pruned_at: string | null;
            /** Turn Count */
            turn_count: number;
        };
        /** ShipmentEventView */
        ShipmentEventView: {
            /** Event Type */
            event_type: string;
            /** Evidence Id */
            evidence_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Note */
            note: string | null;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
        };
        /** ShipmentView */
        ShipmentView: {
            /** Carrier */
            carrier: string;
            /** Delivered At */
            delivered_at: string | null;
            /** Estimated Delivery */
            estimated_delivery: string | null;
            /** Evidence Id */
            evidence_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Shipped At */
            shipped_at: string | null;
            /** Status */
            status: string;
            /** Tracking Reference */
            tracking_reference: string | null;
        };
        /** SystemInfo */
        SystemInfo: {
            /** Agent Enabled */
            agent_enabled: boolean;
            /** App Env */
            app_env: string;
            bounds: components["schemas"]["RunBounds"];
            /**
             * Business Date
             * Format: date
             */
            business_date: string;
            /** Embedding Mode */
            embedding_mode: string;
            /** Llm Model */
            llm_model: string;
            /** Llm Provider */
            llm_provider: string;
            /** Prompt Version */
            prompt_version: string;
            /** Scripted Model */
            scripted_model: boolean;
            /** Writes Enabled */
            writes_enabled: boolean;
        };
        /** TaskResponse */
        TaskResponse: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Deduplicated
             * @default false
             */
            deduplicated: boolean;
            /** Idempotency Key */
            idempotency_key: string;
            /**
             * Order Id
             * Format: uuid
             */
            order_id: string;
            /**
             * Organization Id
             * Format: uuid
             */
            organization_id: string;
            /** Status */
            status: string;
            /** Task Id */
            task_id: string;
        };
        /** ToolOutcome */
        ToolOutcome: {
            /** Error */
            error: number;
            /** Ok */
            ok: number;
        };
        /**
         * Usefulness
         * @enum {string}
         */
        Usefulness: "useful" | "partly" | "not_useful";
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /**
         * ValidationView
         * @description Pre-strip citation accounting (ADR 0010).
         */
        ValidationView: {
            /** Citations Removed */
            citations_removed: number;
            /** Cited */
            cited: number;
            /** Findings Removed */
            findings_removed: number;
            /** Granted */
            granted: number;
            /** Supported Ratio */
            supported_ratio: number | null;
            /** Unsupported */
            unsupported: string[];
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    list_actions_actions_get: {
        parameters: {
            query?: {
                status_filter?: components["schemas"]["ActionStatus"] | null;
                limit?: number;
            };
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActionView"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_action_actions__action_id__get: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path: {
                action_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActionView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_actions__action_id__approve_post: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path: {
                action_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DecisionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActionView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reject_actions__action_id__reject_post: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path: {
                action_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DecisionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActionView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    issue_dev_token_auth_dev_token_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DevTokenRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DevTokenResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_documents_documents_get: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentView"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    upload_document_documents_post: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_upload_document_documents_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocumentView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    read_citation_documents_versions__version_id__chunks__ordinal__get: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path: {
                version_id: string;
                ordinal: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PolicyPassageView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_document_documents__document_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    retry_index_documents__document_id__retry_post: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    me_me_get: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MeResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    summary_metrics_summary_get: {
        parameters: {
            query?: {
                days?: number;
            };
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MetricsSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    lookup_task_ops_tasks_get: {
        parameters: {
            query: {
                idempotency_key: string;
            };
            header?: {
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TaskResponse"] | null;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_task_ops_tasks_post: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateTaskRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TaskResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_orders_orders_get: {
        parameters: {
            query?: {
                overdue_only?: boolean;
                flagged_only?: boolean;
                q?: string | null;
                as_of?: string | null;
                limit?: number;
                offset?: number;
            };
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrderSummary"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    order_counts_orders_counts_get: {
        parameters: {
            query?: {
                as_of?: string | null;
            };
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrderCounts"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_order_orders__order_id__get: {
        parameters: {
            query?: {
                as_of?: string | null;
            };
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path: {
                order_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrderDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_runs_runs_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
                status_filter?: components["schemas"]["RunStatus"] | null;
            };
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunSummary"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_run_runs_post: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateRunRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_runs__run_id__get: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_run_runs__run_id__cancel_post: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    submit_feedback_runs__run_id__feedback_post: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FeedbackRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    system_info_system_get: {
        parameters: {
            query?: never;
            header?: {
                "X-Organization"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SystemInfo"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
