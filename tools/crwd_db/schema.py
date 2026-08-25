"""LLM tool schema for ``crwd_db``."""

from __future__ import annotations

CRWD_DB_SCHEMA = {
    "name": "crwd_db",
    "description": (
        "Query CRWD's MongoDB data: gigs/campaigns, users, campaign "
        "membership, a member's accepted products (buy links), their receipt/"
        "proof upload status, and their account notifications. Read-only apart "
        "from the proof-submission actions below. "
        "list_active_gigs = active joinable gigs the member has NOT joined "
        "(excludes spots-full campaigns where accepted members >= number_of_people, "
        "matching Explore); get_user_gigs / "
        "get_user_gig_status = enrolled/in-progress — if intent between those two is "
        "unclear, ask via clarify before choosing. "
        "Use the specific action if it fits (list_active_gigs, get_gig_details, "
        "get_user, get_user_gigs, get_user_gig_history, get_waitlisted_gigs, get_user_gig_status, "
        "get_user_products, "
        "get_user_receipts, get_user_notifications); use custom_query only when none of the "
        "others answer the question. list_active_gigs accepts user_id to "
        "exclude gigs the member already has a membership for, and offset for "
        "pagination; it returns has_more and next_offset for the next page. "
        "get_gig_details fuzzy-matches gig names and returns ranked candidates "
        "(set full=true or top_n=1 for the full gig payload). Each store carries a "
        "requirements dict (requires_receipt, requires_review_link, requires_review_rating, "
        "requires_ugc_post, ...) — these flags are the gig's proof spec; use them, not "
        "type_of_work_proof, which is unset on almost every gig. "
        "get_user_gig_history returns past membership rows including rejected/completed gigs. "
        "get_waitlisted_gigs returns gigs the member applied for but is not "
        "yet accepted into (isAccepted false / pending acceptance). "
        "get_user_gig_status returns per-gig stage and personalized next_step "
        "from membership + proof progress. Always relay next_step verbatim or "
        "closely paraphrased — never compose your own enrollment/acceptance/"
        "buy-link sentence from raw fields, and never read isApproved (legacy, "
        "unused end-to-end). 'Accepted' = let into the gig; 'approved' = proof "
        "validated and cleared for payout — never use these interchangeably. "
        "In the progress dict, product_assigned means a buy link/product is on "
        "file for them — it is NOT evidence they bought anything, so never say "
        "their purchase is confirmed or their order registered. Proof is "
        "submitted in this chat, never in the CRWD app. "
        "Proof submissions (used by the crwd-proof-validator skill): "
        "get_user_proofs(user_id[, crwd_id][, status]) is what a member has actually "
        "submitted — use it for 'what have I sent?' / 'did my receipt go through?', "
        "and never ask a member for an order number to look up their own proof. "
        "check_duplicate_proof asks whether a proof id is already claimed — pass "
        "user_id AND crwd_id, because a proof id names a purchase, not a submission: "
        "the same member may back one gig with several artifacts of the same purchase "
        "(order screenshot + receipt), but another member using it, or the same member "
        "reusing it on a different gig, is a duplicate. find_proof returns the full "
        "submission history for a proof id across every status; store_proof records one "
        "validated submission (reason_code and reason are required on every status, "
        "accepted included) and sets is_gig_completed itself on the proof that leaves "
        "nothing outstanding. check_gig_proof_completion(user_id, crwd_id) reports which "
        "required artifacts are accepted and which are still outstanding — use it to know "
        "what to coach for, and to decide whether the gig is done. "
        "user_has_completed_gig(user_id) returns whether the member has ever completed a "
        "gig (proofs accepted) — true/false/null (null = lookup failed, don't guess). "
        "Use this for new-user status, not get_user_gig_history, whose membership rows "
        "don't carry proof-completion state. Proof ids are normalized "
        "before comparison, so REC#/Order # prefixes, spacing and hyphens do not matter, "
        "and UGC links key on platform:post_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_active_gigs", "get_gig_details", "get_user",
                    "get_user_gigs", "get_user_gig_history", "get_waitlisted_gigs",
                    "get_user_gig_status",
                    "get_user_products",
                    "get_user_receipts", "get_user_notifications",
                    "store_proof", "get_user_proofs", "check_duplicate_proof",
                    "find_proof", "check_gig_proof_completion",
                    "user_has_completed_gig",
                    "mark_proof_risk_scored", "custom_query",
                ],
            },
            "limit": {"type": "integer", "description": "max rows per page (capped at 20; list_active_gigs default 5; get_user_gig_status default 20)"},
            "offset": {
                "type": "integer",
                "description": (
                    "skip N results for pagination (list_active_gigs). "
                    "Use next_offset from the previous result to get the next page."
                ),
            },
            "identifier": {"type": "string", "description": "email, phone, or user _id (get_user)"},
            "user_id": {
                "type": "string",
                "description": (
                    "users._id. For list_active_gigs: exclude gigs the member "
                    "already has a membership for. Also used by get_user_gigs, "
                    "get_user_gig_history, get_waitlisted_gigs, get_user_products, get_user_receipts, "
                    "get_user_notifications, get_user_gig_status, user_has_completed_gig. "
                    "Required by store_proof; "
                    "optional on check_duplicate_proof (to tell a self-resubmit from another "
                    "member's proof) and find_proof (to filter)."
                ),
            },
            "crwd_id": {
                "type": "string",
                "description": (
                    "Optional gig _id. For get_user_gig_status: filter to that gig. "
                    "For get_user_products: return every product on that gig's catalog "
                    "(plus the member's purchase rows for it), not only one buy_link."
                ),
            },
            "gig_id": {
                "type": "string",
                "description": "Alias of crwd_id for get_user_products",
            },
            "gig_name": {
                "type": "string",
                "description": (
                    "Optional fuzzy gig name filter (get_user_gig_status); "
                    "the gig's name to record alongside the proof (store_proof)"
                ),
            },
            "include_waitlisted": {
                "type": "boolean",
                "description": "Include waitlisted memberships (get_user_gig_status)",
            },
            "query": {"type": "string", "description": "gig _id, name, or free text to fuzzy-match (get_gig_details)"},
            "top_n": {"type": "integer", "description": "max candidates to return, default 3, max 10 (get_gig_details)"},
            "full": {
                "type": "boolean",
                "description": "Return full gig payload for get_gig_details (terms, stores, targeting)",
            },
            "proof_id": {
                "type": "string",
                "description": (
                    "The proof's own identifier, as extracted (store_proof, "
                    "check_duplicate_proof, find_proof). Target REC#, Amazon Order #, "
                    "UGC post link, or for review_screenshot "
                    "'{crwd_id}:{handle}:{review_date}' -- gig id, the reviewer handle "
                    "and the review date exactly as shown on the image (this tool "
                    "slugifies only: it does not parse or validate dates). Reviews are "
                    "never keyed on a url. Normalized before comparison. Never invent "
                    "one: if no identifier can be read, store the proof as needs_human "
                    "with reason_code no_identifier."
                ),
            },
            "proof_type": {
                "type": "string",
                "enum": [
                    "receipt_target", "receipt_amazon", "receipt_other",
                    "order_screenshot", "review_screenshot", "ugc_link",
                ],
                "description": (
                    "The artifact's kind. An order confirmation (order_screenshot) and "
                    "the receipt for that same order are different artifacts of one "
                    "purchase and share an order number — typing them apart is what "
                    "lets a member record both. A review is always a review_screenshot: "
                    "there is no review-link proof type, so a review url is coached into "
                    "a screenshot rather than recorded."
                ),
            },
            "status": {
                "type": "string",
                "enum": ["accepted", "rejected", "needs_human"],
                "description": (
                    "Verdict for this proof (store_proof); optional filter for "
                    "get_user_proofs"
                ),
            },
            "reason_code": {
                "type": "string",
                "enum": [
                    "clean_match", "duplicate_proof", "gig_not_active_for_user",
                    "wrong_proof_type", "incomplete_submission", "date_outside_gig_window",
                    "no_identifier", "invalid_order_number", "wrong_product",
                    "wrong_quantity", "unreadable", "suspected_edited",
                    "link_unreachable", "link_not_owned", "content_mismatch",
                ],
                "description": (
                    "Required on every store_proof, accepted included (use clean_match on "
                    "an accept). Internal only — never tell the member."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "Required on every store_proof: one human-readable line on what "
                    "matched or failed. Internal only — never tell the member."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Confidence the proof is authentic and matches the gig (store_proof)",
            },
            "proof_info": {
                "type": "object",
                "description": (
                    "Everything you read off the proof, shaped by proof_type; stored as "
                    "metadata.proof_info (store_proof). "
                    "Receipts/order screenshots: merchant_name, store_location, purchase_date, "
                    "order_number, total_amount, tax_amount, payment_method, "
                    "line_items[{product_name, quantity, price, amount}]. "
                    "Reviews: platform, rating, review_text, handle, posted_at, verified_purchase. "
                    "UGC: platform, handle, posted_at, likes, comments, views, caption. "
                    "Record what you actually saw — the risk assessment reads this."
                ),
            },
            "product_name": {
                "type": "string",
                "description": "The gig product this proof is for, as matched (store_proof)",
            },
            "store_name": {
                "type": "string",
                "description": "Store the proof came from; normalized on write (store_proof)",
            },
            "source_url": {"type": "string", "description": "Attachment/media URL that was read. Required on an accepted proof (store_proof)"},
            "proof_link": {"type": "string", "description": "Member-supplied review/UGC link, when the proof is a link. Satisfies the evidence requirement on an accept (store_proof)"},
            "proof_record_id": {
                "type": "string",
                "description": "proof_record_id returned by store_proof (mark_proof_risk_scored)",
            },
            "collection": {"type": "string", "enum": [
                "crwds", "users", "added_crwd_members",
                "user_product_purchases", "receipt_upload_history", "notifications",
                "proof_submissions",
            ]},
            "operation": {"type": "string", "enum": ["find", "count"]},
            "filter": {"type": "object"},
            "projection": {"type": "object"},
            "sort": {"type": "object"},
        },
        "required": ["action"],
    },
}
