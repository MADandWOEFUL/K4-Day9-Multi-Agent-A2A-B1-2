import os
import glob
import json
import shutil
from src.data_loader import DataLoader
from src.agent_system import CoordinatorAgent, LLMClient

# Monkey-patch model before instantiating
LLMClient.MODEL_NAME = "qwen/qwen3-8b"

def main():
    model_slug  = "qwen3-8b"
    input_dir   = "input"
    output_dir  = f"output_{model_slug}"
    logging_dir = "logging"
    trace_path  = f"trace_{model_slug}.jsonl"
    logging_trace_path = os.path.join(logging_dir, f"trace_{model_slug}.jsonl")

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(logging_dir, exist_ok=True)

    if os.path.exists(trace_path):
        os.remove(trace_path)
    if os.path.exists(logging_trace_path):
        os.remove(logging_trace_path)

    print(f"Model: {LLMClient.MODEL_NAME}")
    print("Loading Olist dataset into memory...")
    data_loader = DataLoader(data_dir="data")
    print("Dataset successfully loaded!")

    coordinator = CoordinatorAgent(data_loader, trace_path=trace_path)

    input_files = sorted(glob.glob(os.path.join(input_dir, "EC_*.json")))
    print(f"Found {len(input_files)} case input files.")

    success_count = 0
    for file_path in input_files:
        with open(file_path, "r", encoding="utf-8") as f:
            case_input = json.load(f)

        case_id = case_input["case_id"]
        try:
            result = coordinator.process_case(case_input)

            output_file = os.path.join(output_dir, f"{case_id}.json")
            with open(output_file, "w", encoding="utf-8") as out_f:
                json.dump(result, out_f, indent=2, ensure_ascii=False)

            success_count += 1
            print(f"[{success_count}/{len(input_files)}] {case_id} -> {output_file}")
        except Exception as e:
            print(f"ERROR processing {case_id}: {e}")
            raise e

    # Sync trace to logging/
    if os.path.exists(trace_path):
        shutil.copy(trace_path, logging_trace_path)

    print(f"\nDone! {success_count} files -> '{output_dir}/'")
    print(f"Trace: '{trace_path}'")

if __name__ == "__main__":
    main()
