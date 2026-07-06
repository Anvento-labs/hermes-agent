# crwd_staging — MongoDB Collections Reference

Database: `crwd_staging`
Last surveyed: 2026-07-05

---

## Core Entities

### `users` (1,375 docs)
The main user accounts table. Stores every member, admin, and business owner on the platform.

Key fields: `first_name`, `last_name`, `full_name`, `email`, `phone`, `role`, `status`, `isBlocked`, `isEmailVerified`, `register_type`, `sign_up_request_status`

---

### `crwds` (62 docs)
The gig definitions table — each document is one gig/campaign posted on the platform.

Key fields: `name`, `description`, `gig_type` (web_based / irl), `start_date`, `end_date`, `payout`, `price` (product price), `number_of_people`, `status`, `type_of_work_proof`, `client_id`, `business_owner_id`, `address`, `city`, `state`, `isArchived`, `isDeleted`

---

### `clients` (4 docs)
Brand/client accounts that post gigs. Links to the products they sell.

Key fields: `name`, `description`, `image`, `product_ids`, `activation_plans`

---

### `products` (21 docs)
Products associated with clients/brands. Referenced by gigs and purchase/order collections.

Key fields: `name`, `client_id`

---

### `roles` (5 docs)
Role definitions for the platform (e.g. member, admin, business owner).

Key fields: `role`, `roleDisplayName`, `rolegroup`, `status`

---

## User ↔ Gig Relationship

### `added_crwd_members` (2,595 docs)  ⭐ Primary join table
Records every user enrolled in a gig. This is the main user-to-gig relationship table.

Key fields:
- `member` — user_id of the enrolled member
- `crwd_id` — references `crwds._id`
- `business_owner_id` — who owns the gig
- `isAccepted` — admin accepted the member into the gig (`false` = Request Pending Approval, `true` = IN PROGRESS)
- `isApproved` — admin approved the member's enrollment
- `isArrived` — marked as arrived (IRL gigs)
- `hasPaid` — product price has been paid
- `status` — Active / Inactive
- `rejectionReason`, `rejectionNotes` — populated on rejection
- `reviewedAt`, `reviewedBy` — when/who reviewed the enrollment
- `productPricePaid`, `productPricePayoutLinkId` — product payment tracking

---

### `giginvites` (2 docs)
Peer-to-peer gig invitations — one user inviting another to join a gig.

Key fields: `from_user`, `to_user`, `gig_id`, `status` (pending / accepted / rejected)

---

### `added_worker_crwd_members` (0 docs — currently unused)
Intended for worker-type gig enrollments. No data yet.

---

### `worker_crwds` (0 docs — currently unused)
Intended for worker-specific gig definitions. No data yet.

---

## Gig Progress & Proof Submissions

### `gig_store_orders` (492 docs)  ⭐ Progress tracking for IRL gigs
Tracks a user's in-store purchase and receipt submission for a gig. One record per user per gig store.

Key fields:
- `user_id`, `crwd_id`, `store_id`, `product_id`
- `product_name`, `store_name`, `store_address`
- `receipt_file`, `receipt_files` — uploaded receipt proof
- `order_id`, `tracking_id`
- `isApproved` — admin approval status
- `rejectionReason`, `reviewedAt`, `reviewedBy`

---

### `gig_product_reviews` (829 docs)  ⭐ Progress tracking for review submissions
Tracks a user's product review submission for a gig (review link, rating, UGC post).

Key fields:
- `user_id`, `crwd_id`, `store_id`, `product_id`
- `product_name`, `store_name`
- `review_link` — URL of the posted review
- `review_rating`
- `review_receipt_files` — supporting files
- `ugc_post_link` — UGC content post link
- `isApproved`, `rejectionReason`, `reviewedAt`, `reviewedBy`

---

### `order_receipt_reviews` (4 docs)  Progress tracking for web-based / Amazon gigs
Tracks order receipt uploads and review submissions for online gigs.

Key fields:
- `order_generated_by` — user_id
- `crwd_id`
- `order_number`, `order_receipt_file`
- `review`, `rating`, `review_file`
- `type` — order_receipt / review
- `isOrderApproved`, `status`
- `isSignWaiverAccepted`, `latitude`, `longitude`

---

### `user_product_purchases` (36 docs)
Records when a user confirms purchasing a product as part of a gig. Tracks the buy link used.

Key fields:
- `user_id`, `crwd_id`, `product_id`, `store_id`
- `product_name`, `product_url`, `product_key`
- `gig_type`, `store_name`, `source`, `purchasedAt`

---

### `receipt_upload_history` (69 docs)
Audit log of every receipt file uploaded — includes fraud scoring and validation results.

Key fields:
- `user_id`, `campaign_id`
- `receipt_s3_key`, `receipt_type`
- `status`, `fail_reason`, `violation_details`, `validation_results`
- `matched_store`, `extracted_data`, `order_number`
- `fraud_score_delta`, `fraud_score_after`, `fraud_band_after`

---

### `receipt_hash` (0 docs — currently unused)
Intended for deduplication of receipt uploads by hash. No data yet.

---

### `gig_orders` (0 docs — currently unused)
Placeholder for generic gig order tracking. No data yet.

### `gig_step_submissions` (0 docs — currently unused)
Placeholder for step-by-step gig submission tracking. No data yet.

### `gig_deliverables` (0 docs — currently unused)
Placeholder for gig deliverable tracking. No data yet.

### `gig_reviews` (0 docs — currently unused)
Placeholder for gig-level reviews. No data yet.

### `orders` (0 docs — currently unused)
Generic orders table, currently empty.

---

## Payments & Payouts

### `dots_payout_links` (1,280 docs)
Dot-generated payout links sent to users. One record per payout issued.

Key fields:
- `user_id`, `dots_payout_link_id`
- `link` — the payout URL sent to the user
- `amount`, `currency`, `status`
- `delivery`, `tax_exempt`, `force_collect_compliance_information`
- `metadata`, `idempotency_key`, `additional_steps`

---

### `payments` (0 docs — currently unused)
Generic payments table, currently empty.

### `payouts` (0 docs — currently unused)
Generic payouts table, currently empty.

### `payout_audit_logs` (0 docs — currently unused)
Intended for payout audit trail. No data yet.

### `ledger` (0 docs — currently unused)
Intended for a financial ledger. No data yet.

### `member_dues` (0 docs — currently unused)
Intended for member dues tracking. No data yet.

### `transfers` (0 docs — currently unused)
Intended for transfer records. No data yet.

### `stripeaccounts` (0 docs — currently unused)
Intended for Stripe account records. No data yet.

---

## Notifications & Logs

### `activity_logs` (4,649 docs)
Audit trail of all admin and system actions on gigs and members.

Key fields: `action`, `crwd_id`, `crwd_name`, `user_id`, `user_name`, `user_email`, `performed_by`, `performed_by_name`, `added_crwd_member_id`, `metadata`, `notes`

---

### `admin_notifications` (1,339 docs)
In-app notifications sent to admins (e.g. new submission, review needed).

Key fields: `type`, `user_id`, `crwd_id`, `submission_id`, `store_name`, `product_name`, `isSeen`

---

### `notifications` (15 docs)
Push/device notifications sent to users.

Key fields: `to`, `from`, `title`, `description`, `deviceToken`, `deviceType`, `webDeviceToken`, `notificationType`, `isSeen`

---

### `smslogs` (32,174 docs)
Log of every SMS sent via the platform.

Key fields: `phone`, `message`, `user_id`, `user_name`, `user_email`, `sent_by`, `status`, `batch_id`

---

## Messaging & Chat

### `chat_rooms` (677 docs)
Chat room definitions — supports direct and group chat, including admin support chats.

Key fields: `members`, `room_type`, `created_by`, `is_admin_chat`, `chat_status`, `priority`, `last_message_text`, `last_message_at`, `unseen_count`

---

### `chats` (890 docs)
Individual chat messages within a room.

Key fields: `sender_id`, `receiver_id`, `room_id`, `message`, `file`, `file_type`, `message_status`, `reply_to`, `is_admin_message`, `reaction_type`

---

### `groupmessages` (3 docs)
Bulk messages sent to a group/audience by admins.

Key fields: `group_id`, `message`, `sent_by`, `sent_by_name`, `batch_id`, `recipient_count`, `sent_count`, `failed_count`

---

### `messagegroups` (9 docs)
Groupings used for bulk messaging — can target an audience segment, a gig, or a list of member IDs.

Key fields: `name`, `description`, `type`, `audience_segment_id`, `crwd_id`, `member_ids`, `created_by`

---

### `user_conversations` (10 docs)
Links users to external support/chat conversations (e.g. Chatwoot).

Key fields: `user_id`, `pubsub_token`, `conversation_id`, `conversation_type`, `source_id`

---

## Community & Social

### `connectgroups` (13 docs)
Community groups that members can join. Includes a linked chat room.

Key fields: `name`, `description`, `category`, `tags`, `members`, `created_by`, `chat_room_id`, `focus`, `specialty`, `geography`, `gigs_completed`, `repeat_hire_rate`, `past_activations`, `is_trending`

---

### `userconnections` (287 docs)
Friend/follow connections between users.

Key fields: `from_user`, `to_user`, `status`

---

### `groupactivities` (2 docs)
Activity events within connect groups (joins, posts, etc.).

Key fields: `group_id`, `user_id`, `type`, `metadata`

---

## User Profile & Onboarding

### `interests` (12,058 docs)
User-selected interests/tags used for personalization and matching.

Key fields: `user_id`, `title`, `status`

---

### `interest_options` (24 docs)
The master list of interest categories available for selection.

Key fields: `title`, `icon`, `displayOrder`, `status`

---

### `questionnaires` (27 docs)
Onboarding/profile questions shown to users.

Key fields: `question`, `status`

---

### `useranswers` (31,426 docs)
User responses to questionnaire questions.

Key fields: `user_id`, `question_id`, `answer`, `category`

---

### `audiencesegments` (1 doc)
Defined audience segments used for targeting gig invites or bulk messages.

Key fields: `name`, `description`, `rules`, `status`

---

## Invites & Access

### `invite_codes` (530 docs)
Referral/invite codes generated by users to bring new members onto the platform.

Key fields: `user_id`, `code`, `used_by`, `status`

---

### `business_owner_invites` (0 docs — currently unused)
Intended for business owner invite flows. No data yet.

### `user_invites` (0 docs — currently unused)
Generic user invite records. No data yet.

---

## Moderation & Safety

### `blockedphones` (335 docs)
Phone numbers blocked from signing up or using the platform.

Key fields: `phone`, `reason`, `blocked_by`, `status`

---

### `block_users` (0 docs — currently unused)
Intended for user-level blocking (user blocks another user). No data yet.

---

## Platform Config & Misc

### `platforms` (2 docs)
Platform definitions (e.g. Amazon, Walmart) referenced by gigs and stores.

Key fields: `name`, `slug`, `description`, `icon`, `status`

---

### `settings` (1 doc)
Global application settings stored as key-value pairs.

Key fields: `setting_slug`, `setting_name`, `setting_value`, `status`

---

### `cms` (0 docs — currently unused)
Intended for CMS/content management. No data yet.

### `faqs` (0 docs — currently unused)
Intended for FAQ content. No data yet.

### `instructions` (0 docs — currently unused)
Intended for gig instructions content. No data yet.

### `remindertemplates` (0 docs — currently unused)
Intended for reminder message templates. No data yet.

### `review_ratings` (0 docs — currently unused)
Intended for review rating aggregates. No data yet.

### `reviews` (0 docs — currently unused)
Generic reviews table, currently empty.

### `goals` (0 docs — currently unused)
Intended for goal tracking. No data yet.

### `campaigns` (0 docs — currently unused)
Intended for campaign management (separate from `crwds`). No data yet.

### `categories` (0 docs — currently unused)
Intended for product/gig category definitions. No data yet.

### `supportareas` (0 docs — currently unused)
Intended for support area definitions. No data yet.

### `contact-details` (0 docs — currently unused)
Intended for contact information records. No data yet.

---

## Collection Count Summary

| Collection | Docs | Status |
|---|---|---|
| smslogs | 32,174 | Active |
| useranswers | 31,426 | Active |
| interests | 12,058 | Active |
| activity_logs | 4,649 | Active |
| admin_notifications | 1,339 | Active |
| dots_payout_links | 1,280 | Active |
| users | 1,375 | Active |
| gig_product_reviews | 829 | Active |
| chats | 890 | Active |
| chat_rooms | 677 | Active |
| invite_codes | 530 | Active |
| gig_store_orders | 492 | Active |
| blockedphones | 335 | Active |
| userconnections | 287 | Active |
| added_crwd_members | 2,595 | Active |
| receipt_upload_history | 69 | Active |
| crwds | 62 | Active |
| user_product_purchases | 36 | Active |
| questionnaires | 27 | Active |
| products | 21 | Active |
| notifications | 15 | Active |
| connectgroups | 13 | Active |
| user_conversations | 10 | Active |
| messagegroups | 9 | Active |
| interest_options | 24 | Active |
| roles | 5 | Active |
| clients | 4 | Active |
| order_receipt_reviews | 4 | Active |
| groupmessages | 3 | Active |
| giginvites | 2 | Active |
| groupactivities | 2 | Active |
| platforms | 2 | Active |
| audiencesegments | 1 | Active |
| settings | 1 | Active |
| *many others* | 0 | Unused/placeholder |




  Here's the full picture from the actual data:                             
                                                                               
     User <-> Gig relationship: added_crwd_members                             
                                                                               
     This is the primary join table. It links a user (member field =           
     user_id) to a gig (crwd_id) and tracks enrollment state. Key fields:      
                                                                               
     - member — user_id                                                        
     - crwd_id — references the crwds collection (the gig)                     
     - isAccepted, isApproved, hasPaid, status — enrollment/approval state     
     - isDeleted, rejectionReason, rejectionNotes                              
                                                                               
     2,595 records in staging. crwds is the gig definition collection (62      
     gigs).                                                                    
                                                                               
     Also worth knowing: giginvites tracks peer-to-peer gig invites            
     (from_user → to_user for a gig_id), and user_product_purchases            
     records when a user purchases a product as part of a gig.                 
                                                                               
     User progress in a gig: two collections depending on gig type             
                                                                               
     - gig_store_orders (492 docs) — for in-store / IRL gigs. Tracks a         
     user's purchase+receipt submission per gig. Fields: user_id, crwd_id,     
     product_id, store_id, receipt_file, isApproved, rejectionReason,          
     reviewedAt.                                                               
                                                                               
     - order_receipt_reviews (4 docs) — for web-based / online gigs            
     (Amazon etc.). Tracks order receipt uploads and reviews. Fields:          
     order_generated_by (user_id), crwd_id, order_number,                      
     order_receipt_file, review, rating, isOrderApproved, status.              
                                                                               
     So the short answer:                                                      
                                                                               
     - Relationship → added_crwd_members (join between users and               
     crwds/gigs)                                                               
     - Progress → gig_store_orders for IRL gigs, order_receipt_reviews for     
     online gigs                    