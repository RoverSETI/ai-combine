import os
import sys
import time
import subprocess
import queue
import socket
import torch
import torchaudio
from transformers import AutoModel, AutoConfig
import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav
import requests
from datetime import datetime

# ==================== КОНФИГУРАЦИЯ СИСТЕМЫ ====================
MODEL_PATH = "Qwen2.5-7B-Instruct-Q8_0.gguf"
CONTEXT_SIZE = 4096  
GRID_LAYERS = 99     

LOG_FILE = "transcription_log.txt"
MD_FILE = "formatted_session.md"
LLM_URL = "http://localhost:8080/v1/chat/completions"

LLM_PROMPT = """Ты — опытный редактор русского текста. Тебе приходит сырая транскрипция устной речи.
Сделай следующее:
1. Исправь орфографию, грамматику и пунктуацию.
2. Убери слова-паразиты, заикания и повторы.
3. Разбей текст на логичные абзацы.
4. Сохрани изначальный смысл и стиль автора.
5. Верни ТОЛЬКО Markdown без твоих комментариев и вступлений."""

device = "cuda" if torch.cuda.is_available() else "cpu"
llama_process = None

# ==================== УПРАВЛЕНИЕ СЕРВЕРОМ И ИИ ====================
def start_llama_server():
    global llama_process
    if llama_process is not None:
        return
        
    if not os.path.exists(MODEL_PATH):
        print(f"[❌] ОШИБКА: Файл '{MODEL_PATH}' не найден!"); sys.exit(1)
        
    print(f"[🤖] Запуск редактора Qwen на GPU...")
    # ФИКС: Заменили невалидный '--ngl' на поддерживаемый короткий флаг '-ngl'
    cmd = ["/usr/bin/llama-server", "-m", MODEL_PATH, "-c", str(CONTEXT_SIZE), "-ngl", str(GRID_LAYERS), "--port", "8080"]
    
    try:
        # Снова прячем вывод в DEVNULL, так как аргументы теперь 100% верны
        llama_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        start_time = time.time()
        port_ready = False
        while time.time() - start_time < 20:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                if sock.connect_ex(("localhost", 8080)) == 0:
                    port_ready = True
                    break
            time.sleep(0.5)
            
        if port_ready:
            print("[🟢] Текстовый ИИ Qwen успешно подключен и начал редактуру.")
        else:
            print("[❌] Ошибка: Сервер не смог запуститься на порту 8080.")
    except Exception as e:
        print(f"[❌] Ошибка старта сервера: {e}"); sys.exit(1)

def stop_llama_server():
    global llama_process
    if llama_process:
        llama_process.terminate()
        llama_process.wait()
        llama_process = None
def load_asr_model():
    print(f"[INFO] Стек PyTorch: {device}\n[INFO] Загрузка модели Сбера GigaAM-v3...")
    torch.nn.Module.all_tied_weights_keys = property(lambda self: {})
    
    orig_load = torch.load
    orig_empty = torch.empty
    orig_ones = torch.ones
    orig_zeros = torch.zeros

    torch.load = lambda *a, **kw: orig_load(*a, map_location="cpu") if kw.get("map_location") == "meta" else orig_load(*a, **kw)
    torch.empty = lambda *a, **kw: orig_empty(*a, device="cpu") if kw.get("device") == "meta" else orig_empty(*a, **kw)
    torch.ones = lambda *a, **kw: orig_ones(*a, device="cpu") if kw.get("device") == "meta" else orig_ones(*a, **kw)
    torch.zeros = lambda *a, **kw: orig_zeros(*a, device="cpu") if kw.get("device") == "meta" else orig_zeros(*a, **kw)

    try:
        config = AutoConfig.from_pretrained("ai-sage/GigaAM-v3", trust_remote_code=True)
        model = AutoModel.from_pretrained("ai-sage/GigaAM-v3", revision="e2e_rnnt", config=config, trust_remote_code=True, low_cpu_mem_usage=False, _fast_init=False)
    finally:
        torch.load, torch.empty, torch.ones, torch.zeros = orig_load, orig_empty, orig_ones, orig_zeros
    return model.to(device)

def send_to_llm_editor(raw_text):
    start_llama_server()
    payload = {"messages": [{"role": "system", "content": LLM_PROMPT}, {"role": "user", "content": raw_text}], "temperature": 0.2, "stream": False}
    try:
        res = requests.post(LLM_URL, json=payload, timeout=120)
        if res.status_code == 200: 
            return res.json()['choices']['message']['content'].strip()
    except Exception as e: 
        print(f"\n[⚠️ Ошибка ИИ]: Не удалось отредактировать текст. Тех. инфо: {e}")
    finally:
        stop_llama_server()
    return raw_text

# ==================== РЕЖИМЫ ТРАНСКРИБАЦИИ ====================
def run_file_transcription(file_path, asr_model):
    file_path = file_path.strip().strip("'\"").replace("\\ ", " ")
    if not os.path.exists(file_path): print("[❌] Файл не найден!"); return
    print(f"[🎵] Импорт аудиофайла: {file_path}...")
    try: waveform, sample_rate = torchaudio.load(file_path)
    except Exception as e: print(f"[❌] Ошибка чтения файла: {e}"); return
    if sample_rate != 16000: waveform = torchaudio.transforms.Resample(orig_freq=sample_rate, new_freq=16000)(waveform)
    if waveform.shape[0] > 1: waveform = torch.mean(waveform, dim=0, keepdim=True)
    
    audio_np = waveform.numpy().flatten()
    chunk_samples = 16000 * 20
    raw_text_pieces = []
    temp_chunk = "file_cache_chunk.wav"
    
    print("[🚀] Посегментное локальное распознавание...")
    for idx, start_idx in enumerate(range(0, len(audio_np), chunk_samples)):
        chunk_data = audio_np[start_idx:start_idx + chunk_samples]
        if len(chunk_data) < 16000 * 0.5: continue
        torchaudio.save(temp_chunk, torch.from_numpy(chunk_data).unsqueeze(0), 16000)
        with torch.no_grad():
            try:
                text = asr_model.transcribe(temp_chunk)
                if text and text.strip():
                    clean_segment = text.strip()
                    print(f"[Кусок {idx}]: {clean_segment}")
                    raw_text_pieces.append(clean_segment)
            except Exception as e: print(f"[❌] Ошибка куска {idx}: {e}")
            
    if os.path.exists(temp_chunk): os.remove(temp_chunk)
    raw_text = " ".join(raw_text_pieces)
    if not raw_text.strip(): print("[⚠️] Речь не распознана."); return
    
    del asr_model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    time.sleep(2)
    
    print("[🤖] Передача склеенной стенограммы в Qwen для финальной редактуры...")
    final_md = send_to_llm_editor(raw_text)
    
    output_file = os.path.splitext(file_path)[0] + ".md"
    f_out = open(output_file, "w", encoding="utf-8")
    f_out.write(f"# Анализ стенограммы\n**Дата:** {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n{final_md}")
    f_out.close()
    print(f"[🎉] Документ успешно сохранен в: {output_file}")

def run_live_microphone(asr_model):
    print("\n=== 🎙️ ДОСТУПНЫЕ ИСТОЧНИКИ ЗВУКА В MANJARO ===")
    try:
        devices = sd.query_devices()
        input_nodes = [idx for idx, d in enumerate(devices) if d.get('max_input_channels', 0) > 0]
        for idx in input_nodes: print(f"[{idx}] {devices[idx]['name']}")
        user_choice = input(f"\nВведите номер микрофона (или Enter): ").strip()
        sd.default.device = int(user_choice) if user_choice.isdigit() and int(user_choice) in input_nodes else None
    except Exception: sd.default.device = None
    print("[🟢] Активирован аудиовход.")

    SAMPLE_RATE, CHUNK_DURATION = 16000, 1.5
    CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)
    print("\n[🚀] Запись активна. Для завершения нажмите Ctrl+C.\n" + "-"*60)
    audio_queue = queue.Queue()
    def audio_callback(indata, frames, time_info, status): audio_queue.put(indata.copy())
    audio_buffer, text_batch = [], []
    accumulated_phrase, temp_stream_chunk = "", "live_cache.wav"
    silence_tracker = time.time()
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, callback=audio_callback):
            while True:
                while not audio_queue.empty(): audio_buffer.append(audio_queue.get())
                if text_batch and (time.time() - silence_tracker > 5.0):
                    print("\n[⏱️] Пауза речи. Форматирование текста в Qwen...")
                    edited_block = send_to_llm_editor(" ".join(text_batch))
                    print(f"✨ ИИ-РЕДАКТОР:\n{edited_block}\n" + "-"*60)
                    f_md = open(MD_FILE, "a", encoding="utf-8"); f_md.write(edited_block + "\n\n---\n\n"); f_md.close()
                    text_batch = []
                if len(audio_buffer) * (CHUNK_SIZE / SAMPLE_RATE) >= CHUNK_DURATION:
                    combined_audio = np.concatenate(audio_buffer, axis=0).flatten()
                    wav.write(temp_stream_chunk, SAMPLE_RATE, combined_audio)
                    with torch.no_grad():
                        try: recognized_text = asr_model.transcribe(temp_stream_chunk)
                        except Exception: recognized_text = ""
                    if recognized_text and recognized_text != accumulated_phrase: accumulated_phrase = recognized_text
                    if recognized_text and (recognized_text.endswith(".") or recognized_text.endswith("?") or recognized_text.endswith("!")):
                        clean_phrase = recognized_text.strip(); timestamp = time.strftime('%H:%M:%S')
                        print(f"[{timestamp}] СЫРОЙ ЛОГ: {clean_phrase}")
                        f_log = open(LOG_FILE, "a", encoding="utf-8"); f_log.write(f"[{timestamp}] {clean_phrase}\n"); f_log.close()
                        text_batch.append(clean_phrase); silence_tracker = time.time()
                        if len(text_batch) >= 4:
                            edited_block = send_to_llm_editor(" ".join(text_batch))
                            print(f"✨ ИИ-РЕДАКТОР:\n{edited_block}\n" + "-"*60)
                            f_md2 = open(MD_FILE, "a", encoding="utf-8"); f_md2.write(edited_block + "\n\n---\n\n"); f_md2.close()
                            text_batch = []
                        audio_buffer, accumulated_phrase = [], ""
                time.sleep(0.05)
    except KeyboardInterrupt:
        if os.path.exists(temp_stream_chunk): os.remove(temp_stream_chunk)

if __name__ == "__main__":
    try:
        asr_engine = load_asr_model()
        print("\n=== ВЫБЕРИТЕ РЕЖИМ РАБОТЫ ===")
        print(" 1. Текст из ГОТОВОГО АУДИОФАЙЛА (mp3, wav, flac, m4a)")
        print(" 2. Живая запись С МИКРОФОНА в реальном времени")
        user_mode = input("\nВаш выбор (1 или 2): ").strip()
        if user_mode == "1":
            target_file = input("Имя или путь файла: ").strip()
            run_file_transcription(target_file, asr_engine)
        elif user_mode == "2":
            f1 = open(LOG_FILE, "a", encoding="utf-8"); f1.write(f"\n--- Сессия: {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n"); f1.close()
            f2 = open(MD_FILE, "a", encoding="utf-8"); f2.write(f"\n# Сессия от {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"); f2.close()
            run_live_microphone(asr_engine)
        else: print("[❌] Ошибка: Неверный режим.")
    finally:
        stop_llama_server()
        print("[👋] Видеопамять полностью очищена.")
