"""Monitor literal brand mentions in DeepSeek answers; not sentiment or ranking."""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openai import OpenAI, OpenAIError

BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
BRAND_NAME = "维情"
QUESTIONS = [
    "上海婚姻出现危机，如何选择合适的婚姻咨询机构？",
    "夫妻长期沟通不畅，上海有哪些婚姻家庭咨询机构可以考虑？",
    "想在上海寻求伴侣关系咨询，挑选机构时应比较哪些服务和资质？",
]
COLUMNS = [
    "问题", "回答摘要", "是否提及品牌", "状态", "问题含品牌",
    "模型", "记录时间", "数据来源", "错误类型",
]


def csv_safe(value):
    """Prevent untrusted text from becoming a spreadsheet formula."""
    if isinstance(value, str):
        first = value.lstrip()[:1]
        if (first and first in "=+-@") or value.startswith(("\t", "\r", "\n")):
            return "'" + value
    return value


def save_results(results, output):
    output = Path(output)
    temporary = output.with_name(output.name + ".tmp")
    safe_rows = [{key: csv_safe(value) for key, value in row.items()} for row in results]
    pd.DataFrame(safe_rows, columns=COLUMNS).to_csv(
        temporary, index=False, encoding="utf-8-sig"
    )
    temporary.replace(output)


def run_monitor(client, model, output, questions=None, brand=BRAND_NAME):
    if not brand.strip():
        raise ValueError("品牌词不能为空")
    questions = QUESTIONS if questions is None else questions
    if not questions or any(not isinstance(q, str) or not q.strip() for q in questions):
        raise ValueError("问题列表必须包含非空字符串")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    print(f"开始监控：模型={model}，品牌={brand}，共 {len(questions)} 个问题", flush=True)

    for index, question in enumerate(questions, 1):
        print(f"[{index}/{len(questions)}] 正在提问：{question}", flush=True)
        row = {
            "问题": question, "回答摘要": "", "是否提及品牌": None,
            "状态": "Error", "问题含品牌": brand in question,
            "模型": model, "记录时间": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "数据来源": "DeepSeek API（未启用联网）", "错误类型": "",
        }
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": question}],
                temperature=0,
                max_tokens=1024,
                stream=False,
                extra_body={"thinking": {"type": "disabled"}},
            )
            if not response.choices:
                raise ValueError("EmptyChoices")
            choice = response.choices[0]
            answer = choice.message.content
            if isinstance(answer, str):
                clean_answer = " ".join(answer.split())
                row["回答摘要"] = clean_answer[:50] + ("…" if len(clean_answer) > 50 else "")
            if choice.finish_reason != "stop":
                raise ValueError("IncompleteResponse:" + str(choice.finish_reason))
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("EmptyAnswer")
            mentioned = brand in answer
            row.update({"是否提及品牌": mentioned, "状态": "Positive" if mentioned else "Missed"})
        except OpenAIError as exc:
            # Raw API errors may contain request details; export only type and HTTP status.
            status_code = getattr(exc, "status_code", None)
            row["错误类型"] = type(exc).__name__ + (f":HTTP{status_code}" if status_code else "")
        except ValueError as exc:
            row["错误类型"] = str(exc)

        results.append(row)
        # Save each completed question so later request failures do not erase prior results.
        save_results(results, output)
        detail = row["错误类型"] or ("提及品牌（不代表正面评价）" if row["是否提及品牌"] else "未提及品牌")
        print(f"  {row['状态']}: {detail}", flush=True)

    errors = sum(row["状态"] == "Error" for row in results)
    unprompted = [row for row in results if row["状态"] != "Error" and not row["问题含品牌"]]
    hits = sum(row["是否提及品牌"] for row in unprompted)
    rate = f"{hits}/{len(unprompted)} ({hits / len(unprompted):.1%})" if unprompted else "N/A（无成功的无品牌提示样本）"
    print(f"完成：成功 {len(results) - errors}/{len(results)}，失败 {errors}；无品牌提示样本提及率：{rate}", flush=True)
    print(f"报表：{output.resolve()}", flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("geo_result.csv"), help="CSV 输出路径；同名文件会替换")
    args = parser.parse_args()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    model = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL).strip()
    if not api_key or not model:
        print("配置错误：请设置非空 DEEPSEEK_API_KEY；DEEPSEEK_MODEL 如设置也不能为空。", file=sys.stderr)
        return 2
    try:
        with OpenAI(api_key=api_key, base_url=BASE_URL, timeout=30.0, max_retries=1) as client:
            results = run_monitor(client, model, args.output)
        return 1 if any(row["状态"] == "Error" for row in results) else 0
    except KeyboardInterrupt:
        print("\n运行已中断；已完成的问题保存在报表中。若尚未完成首问，报表未生成。", file=sys.stderr)
        return 130
    except (OSError, ValueError) as exc:
        print(f"运行失败：{type(exc).__name__}。请检查输出路径、写入权限和配置。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
