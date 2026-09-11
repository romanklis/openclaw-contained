#!/usr/bin/env bash
# Mongo init: create application users (RBAC) then load the banking_docs dataset.
# Runs during the MongoDB initdb phase (temporary server, auth not yet enforced).
set -euo pipefail

AI_U="${MONGO_DMS_AI_USER:-dms_ai}"
AI_P="${MONGO_DMS_AI_PASSWORD:-dms_ai_pwd_demo}"
ARCH_U="${MONGO_DMS_ARCHIVIST_USER:-dms_archivist}"
ARCH_P="${MONGO_DMS_ARCHIVIST_PASSWORD:-dms_archivist_pwd_demo}"

mongosh --quiet mongodb://127.0.0.1:27017/admin --eval '
  const adminDb = db.getSiblingDB("admin");
  // Custom role: the AI agent may read all banking_docs collections and insert
  // into credit_memos (its recommendation draft), but nothing else.
  try {
    adminDb.createRole({
      role: "bankingDocsAgentReader",
      privileges: [
        { resource: { db: "banking_docs", collection: "" }, actions: ["find"] },
        { resource: { db: "banking_docs", collection: "credit_memos" }, actions: ["insert", "find"] }
      ],
      roles: []
    });
  } catch (e) { if (e.codeName !== "Location51005") throw e; }
  try {
    adminDb.createUser({ user: "'"$AI_U"'", pwd: "'"$AI_P"'",
      roles: [{ role: "bankingDocsAgentReader", db: "admin" }] });
  } catch (e) { if (e.codeName !== "Location51007") throw e; }
  // The human archivist may read and append to the archive.
  try {
    adminDb.createRole({
      role: "bankingDocsArchivist",
      privileges: [
        { resource: { db: "banking_docs", collection: "credit_decision_archive" }, actions: ["find", "insert", "update"] },
        { resource: { db: "banking_docs", collection: "loan_applications" }, actions: ["find", "update"] },
        { resource: { db: "banking_docs", collection: "credit_memos" }, actions: ["find"] },
        { resource: { db: "banking_docs", collection: "policies" }, actions: ["find"] }
      ],
      roles: []
    });
  } catch (e) { if (e.codeName !== "Location51005") throw e; }
  try {
    adminDb.createUser({ user: "'"$ARCH_U"'", pwd: "'"$ARCH_P"'",
      roles: [{ role: "bankingDocsArchivist", db: "admin" }] });
  } catch (e) { if (e.codeName !== "Location51007") throw e; }
'

mongosh --quiet mongodb://127.0.0.1:27017/banking_docs /init-mongo/01-init.js
echo "banking_docs initialized"
