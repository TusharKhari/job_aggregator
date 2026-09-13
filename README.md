# job_aggregator

## ⚡ One-Command Full Pipeline (Scrape & Sync to Google Sheets)

Run everything in a single command (aggregates all 20 queries, uploads to Google Sheets tab `aggJ`, and saves logs):

```bash
./run_pipeline.sh
```

---

## 🛠 Manual Run Options

# 1. Run top 5 high-priority queries across Baden-Württemberg & Bayern (24h filter ON)
python aggregator.py

# 2. Run all queries in a specific category from search_config.json
python aggregator.py --category computer_vision
python aggregator.py --category llm_nlp_genai
python aggregator.py --category software_and_data

# 3. Run all high-priority queries (pass --limit-queries 0 for unlimited)
python aggregator.py --limit-queries 0

# 4. Fast scan (skip full description page downloads for faster discovery)
python aggregator.py --no-desc

# 5. Run only Indeed or only Xing
python aggregator.py -s indeed
python aggregator.py -s xing
