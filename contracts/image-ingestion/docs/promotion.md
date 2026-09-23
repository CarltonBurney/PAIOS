# Canonical promotion and deployment gates

This folder and ZIP are reviewable local delivery artifacts. They have not been uploaded to OneDrive, DGE, or Obsidian. No drive/vault IDs were supplied. Do not label this release production-approved until the following promotion is recorded.

1. Configure exact Obsidian vault/section, DGE canonical contracts/records roots, OneDrive working root, service identities and workspace access policy. Confirm the vault's persistence arrangement does not rely on an unbacked local disk.
2. Review and accept the new detailed contract decisions with the architecture owner. Capture the chosen database/coordinator, first OCR provider/profile, hosting, retention and limits. These choices do not block interface work; they block deployment that assumes them.
3. Place the release and checksum manifest in the configured canonical DGE contracts area. Verify all file hashes. Add an Obsidian release note/decision page that points to this same release ID and manifest. Publish a common approved pointer only after both agree. Working iterations in OneDrive never supersede that pointer.
4. Verify service accounts can read approved contracts, persist canonical records, append audit events and access only configured OneDrive roots. Prove coordinator fencing and failure recovery. Configure diagnostics/recovery alert destination without embedding credentials in contracts.
5. Run the acceptance matrix against the implementation and supply evidence to ChatGPT for integration QA. Promote implementation state in the canonical set only after required checks pass.

Change requests must include affected schema/interface fields, reason, compatibility/migration impact, storage impact and revised fixtures. Claude may fix implementation defects without contract changes; shared interface changes return to ChatGPT. Version manifests include contract release, implementation commit, provider/decoder versions, configuration fingerprint and acceptance evidence references. Rollback points readers to a compatible release; it never erases existing Audit events or rewrites completed history.
