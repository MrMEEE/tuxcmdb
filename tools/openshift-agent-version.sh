#!/usr/bin/env bash
set -euo pipefail

if [[ "$(git rev-parse --is-shallow-repository)" == true ]]; then
    git fetch --unshallow --tags origin
else
    git fetch --tags origin
fi

# Exclude prerelease and non-release tags when selecting the nearest ancestor.
release_tags=()
while IFS= read -r tag; do
    if [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        release_tags+=("$tag")
    fi
done < <(git tag --merged HEAD --list 'v*')
if [[ ${#release_tags[@]} -eq 0 ]]; then
    echo "No reachable release tag matching vMAJOR.MINOR.PATCH" >&2
    exit 1
fi
matches=()
for tag in "${release_tags[@]}"; do
    matches+=(--match "$tag")
done
tag="$(git describe --tags --abbrev=0 "${matches[@]}" HEAD)"
distance="$(git rev-list --count "$tag"..HEAD)"
printf '%s.%s\n' "${tag#v}" "$distance"
