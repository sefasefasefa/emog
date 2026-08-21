import subprocess
import sys
from openai import OpenAI

# OmniRoute portu 3001 olarak güncellendi
client = OpenAI(
    base_url="http://localhost:3001/v1",
    api_key="omniroute-local"
)

def run_self_healing_loop(initial_prompt, max_iterations=5):
    conversation_history = [
        {
            "role": "system", 
            "content": "You are an autonomous senior developer agent. Write ONLY valid, executable Python code inside a markdown block ```python ... ``` for the given task. Do not include extra conversational filler."
        },
        {
            "role": "user", 
            "content": initial_prompt
        }
    ]

    for i in range(max_iterations):
        print(f"\n[Döngü] Iterasyon {i+1} / {max_iterations} başlatılıyor...")
        
        try:
            response = client.chat.completions.create(
                model="default",
                messages=conversation_history,
                temperature=0.2
            )
            
            content = response.choices[0].message.content
            print("[Bilgi] Model yanıtı alındı.")
            
            code = extract_code_block(content)
            if not code:
                print("[Hata] Model geçerli bir kod bloğu üretmedi.")
                conversation_history.append({"role": "assistant", "content": content})
                conversation_history.append({"role": "user", "content": "Geçerli bir python kod bloğu (```python ... ```) vermedin. Lütfen sadece çalıştırılabilir kodu ver."})
                continue

            script_filename = "generated_task.py"
            with open(script_filename, "w", encoding="utf-8") as f:
                f.write(code)

            print(f"[İşlem] '{script_filename}' çalıştırılıyor...")
            result = subprocess.run([sys.executable, script_filename], capture_output=True, text=True)

            if result.returncode == 0:
                print("\n[Başarılı] Kod hatasız çalıştı ve tamamlandı!")
                print("Çıktı:\n", result.stdout)
                return True
            else:
                print(f"[Hata Tespit Edildi] Exit Code: {result.returncode}")
                error_output = result.stderr or result.stdout
                print(error_output)

                conversation_history.append({"role": "assistant", "content": content})
                feedback_prompt = f"Yazdığın kod şu hatayı verdi:\n{error_output}\nLütfen hatayı analiz et, eksikleri gider ve düzeltilmiş Python kodunun tamamını tekrar ver."
                conversation_history.append({"role": "user", "content": feedback_prompt})

        except Exception as e:
            print(f"[Kritik Sistem Hatası]: {e}")
            break

    print("\n[Uyarı] Maksimum iterasyon sınırına ulaşıldı ancak görev tam olarak çözülemedi.")
    return False

def extract_code_block(text):
    if "```python" in text:
        parts = text.split("```python")
        if len(parts) > 1:
            code_part = parts[1].split("```")[0]
            return code_part.strip()
    elif "```" in text:
        parts = text.split("```")
        if len(parts) > 1:
            code_part = parts[1].split("```")[0]
            return code_part.strip()
    return None

if __name__ == "__main__":
    task_description = input("Ajana vermek istediğin görevi gir: ")
    run_self_healing_loop(task_description)
