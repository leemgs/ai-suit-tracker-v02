#!/usr/bin/env bash

# 1. 토큰 설정: https://www.courtlistener.com/profile/api-token/
export COURTLISTENER_TOKEN="****************"

# 2. 빠른 API 체크
curl -s -H "Authorization: Token $COURTLISTENER_TOKEN" \
  "https://www.courtlistener.com/api/rest/v4/search/?q=AI&type=r&page_size=1" \
  | jq -r 'if .count then "✅ API 정상 작동" else "❌ API 오류" end'
  
  
  
  
