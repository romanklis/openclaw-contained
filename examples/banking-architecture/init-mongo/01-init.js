// 01-init.js — banking_docs dataset (policies, loan application, empty archive/memos)

const db = db.getSiblingDB("banking_docs");

db.createCollection("policies");
db.createCollection("loan_applications");
db.createCollection("credit_memos");
db.createCollection("credit_decision_archive");

// ---------------------------------------------------------------------------
// Commercial Lending Policy 2026
// ---------------------------------------------------------------------------
db.policies.updateOne(
  { _id: "POL-COM-2026" },
  {
    $set: {
      policy_id: "POL-COM-2026",
      policy_name: "Commercial Lending Policy 2026",
      version: "2026.1",
      effective_date: "2026-01-01",
      issuer: "Group Credit Risk Office",
      status: "ACTIVE",
      overview:
        "Establishes minimum underwriting standards for commercial credit facilities, " +
        "including collateral, cash-flow, concentration and eligibility requirements.",
      scope: ["TERM_LOAN", "REVOLVING_CREDIT", "BRIDGE_FINANCE"],
      parameters: {
        max_ltv_ratio: 0.75,
        min_dscr: 1.25,
        max_unsecured_limit_usd: 5000000,
        max_tenor_years: 7,
        prohibited_industries: ["GAMBLING", "UNREGULATED_CRYPTO"],
      },
      mandatory_controls: [
        "Facilities over USD 1,000,000 require an active APPROVED KYC status.",
        "Debt service coverage (DSCR) must be >= 1.25 using verified financials.",
        "Loan-to-value (LTV) must not exceed 75% on appraised collateral.",
        "No exposure to prohibited industries (GAMBLING, UNREGULATED_CRYPTO).",
      ],
      ai_governance: {
        ai_approval_limit_usd: 100000,
        rule: "The AI credit agent may analyze, draft and recommend, but any facility " +
              "above its approval limit MUST be routed to a HUMAN_CREDIT_OFFICER.",
        human_co_signer_required: true,
      },
    },
  },
  { upsert: true }
);

// ---------------------------------------------------------------------------
// Loan application APP-2026-0899
// ---------------------------------------------------------------------------
db.loan_applications.updateOne(
  { _id: "APP-2026-0899" },
  {
    $set: {
      application_id: "APP-2026-0899",
      customer_id: "C-1001",
      customer_name: "TechFlow Solutions LLC",
      request: {
        amount_usd: 3000000,
        type: "TERM_LOAN",
        purpose: "Expansion of data center infrastructure",
        tenor_years: 5,
      },
      financials: {
        annual_revenue_usd: 15000000,
        ebitda_usd: 3500000,
        existing_annual_debt_service_usd: 500000,
        // Explicit demo methodology: DSCR =
        //   EBITDA / (existing annual debt service + proposed annual debt service)
        // Proposed service computed as a 5y amortizing term at a 5.0% demo rate.
        proposed_annual_debt_service_usd: 700000,
        dscr_methodology_note:
          "DSCR = EBITDA / (existing_annual_debt_service_usd + proposed_annual_debt_service_usd). " +
          "Proposed service is a demo assumption stated on the application (700,000 USD/year).",
      },
      status: "RECEIVED_PENDING_ANALYSIS",
      received_at: "2026-08-20T09:00:00Z",
      submitted_by: "relationship-manager-7",
      audit: {
        created_by: "originations-api",
        updated_at: "2026-08-20T09:00:00Z",
      },
    },
  },
  { upsert: true }
);

// Indexes / helpers used by the append-only archive rule.
db.credit_decision_archive.createIndex({ application_id: 1, version: 1 }, { unique: true });
db.loan_applications.createIndex({ customer_id: 1 });
print("banking_docs dataset loaded");
