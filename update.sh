#!/usr/bin/env bash

set -e

# ===============================
# CONFIG
# ===============================

BASE_BRANCH="main"

# contoh:
# feature/fix-redirecthunter-cli
# fix/database-cleanup
BRANCH_TYPE="${1:-fix}"
BRANCH_NAME="${2:-auto-update-$(date +%Y%m%d-%H%M%S)}"

BRANCH="${BRANCH_TYPE}/${BRANCH_NAME}"

ISSUE_FILE="issue.md"
PR_FILE="pr.md"


# ===============================
# CHECK REQUIREMENTS
# ===============================

command -v git >/dev/null || {
    echo "git tidak ditemukan"
    exit 1
}

command -v gh >/dev/null || {
    echo "GitHub CLI gh tidak ditemukan"
    exit 1
}


# ===============================
# SAFETY CHECK
# ===============================

echo "Checking repository..."

if [ ! -d ".git" ]; then
    echo "Bukan git repository"
    exit 1
fi


echo "Current branch:"
git branch --show-current


# ===============================
# CREATE BRANCH
# ===============================

echo ""
echo "Creating branch:"
echo "$BRANCH"

git checkout "$BASE_BRANCH"
git pull origin "$BASE_BRANCH"

git checkout -b "$BRANCH"


# ===============================
# CREATE ISSUE TEMPLATE
# ===============================

cat > "$ISSUE_FILE" <<EOF
# Issue

## Description

$(cat <<'DESC'
Tuliskan masalah atau perubahan yang diperlukan.

Contoh:

- Fix async generator cleanup
- Add new CLI options
- Update documentation
DESC
)

## Expected Behavior

Jelaskan hasil yang diharapkan.

## Implementation Plan

- [ ] Analyze current implementation
- [ ] Implement fix
- [ ] Add tests
- [ ] Update documentation
- [ ] Run regression test

## Risk

Tuliskan kemungkinan breaking changes.
EOF


echo "Created $ISSUE_FILE"



# ===============================
# CREATE GITHUB ISSUE
# ===============================

echo ""
echo "Creating GitHub issue..."

ISSUE_URL=$(gh issue create \
    --title "$BRANCH_NAME" \
    --body-file "$ISSUE_FILE")


echo "Issue created:"
echo "$ISSUE_URL"



# ===============================
# USER CODE CHANGES HERE
# ===============================

echo ""
echo "====================================="
echo "Lakukan perubahan kode sekarang."
echo ""
echo "Setelah selesai tekan ENTER"
echo "====================================="

read



# ===============================
# SECURITY CHECK
# ===============================

echo ""
echo "Checking sensitive files..."

git status --short


echo ""
echo "Pastikan file berikut tidak ikut:"
echo "- raw_data/"
echo "- *.env"
echo "- database sqlite"
echo "- credential file"

read -p "Lanjut commit? (y/n): " CONFIRM

if [ "$CONFIRM" != "y" ]; then
    echo "Cancelled"
    exit 0
fi



# ===============================
# COMMIT
# ===============================

git add .

git commit \
-m "fix: ${BRANCH_NAME}"


# ===============================
# PUSH
# ===============================

git push \
-u origin "$BRANCH"



# ===============================
# CREATE PR TEMPLATE
# ===============================


cat > "$PR_FILE" <<EOF
# Pull Request

## Related Issue

${ISSUE_URL}

## Summary

- Describe changes here

## Changes

- Fixed:
- Added:
- Updated:

## Testing

- [ ] Unit test passed
- [ ] Regression test passed
- [ ] Manual verification

## Risk

- None / describe risk

## Checklist

- [ ] Documentation updated
- [ ] No sensitive data included
- [ ] Ready for review
EOF


git add "$PR_FILE"

git commit \
-m "docs: add pull request description"

git push



# ===============================
# CREATE PR
# ===============================


echo ""
echo "Creating Pull Request..."

PR_URL=$(gh pr create \
    --title "$BRANCH_NAME" \
    --body-file "$PR_FILE" \
    --base "$BASE_BRANCH" \
    --head "$BRANCH")


echo ""
echo "================================"
echo "PR CREATED"
echo "$PR_URL"
echo "================================"


# ===============================
# WAIT MERGE
# ===============================

echo ""
echo "Menunggu PR merge manual."
echo "Jangan merge otomatis."
echo ""

read -p "Setelah PR sudah di-merge, tekan ENTER untuk cleanup"



# ===============================
# CLEANUP
# ===============================


echo ""
echo "Cleaning branch..."

git checkout "$BASE_BRANCH"

git pull origin "$BASE_BRANCH"


echo "Deleting local branch..."

git branch -d "$BRANCH" || true


echo "Deleting remote branch..."

git push origin \
--delete "$BRANCH" || true


echo ""
echo "================================"
echo "DONE"
echo "Branch cleaned:"
echo "$BRANCH"
echo "================================"
