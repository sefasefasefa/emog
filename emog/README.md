# EMOG — Local agent web app

Kısa: Bu proje, üretken ajan akışını yerel bir Django web uygulaması olarak çalıştırır, OmniRoute (varsayılan: http://localhost:3001) üzerinden modellerle iletişim kurar ve her çalıştırma için dosya tabanlı JSON-lines logları üretir.

## Önkoşullar
- Python 3.11
- Windows (PowerShell örnek komutları aşağıda). Diğer OS'lerde `venv` aktivasyonu platforma göre değişir.

## Hızlı kurulum ve çalıştırma (PowerShell)

1) Sanal ortamı oluşturduysanız aktifleştirin; yoksa oluşturun:

```powershell
# Eğer zaten .venv varsa
& .\.venv\Scripts\Activate.ps1

# Yeni bir venv oluşturmak isterseniz
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
```

2) Bağımlılıkları yükleyin (PEP 668 uyarısı alırsanız `--break-system-packages` kullanın):

```powershell
python -m pip install --break-system-packages -r requirements.txt
```

3) Ortam değişkenlerini ayarlayın (isteğe bağlı `.env` kullanabilirsiniz).

- Örnek `.env` (project kökünde `.env` oluşturun):

```
OMNIROUTE_BASE_URL=http://localhost:3001/v1
OMNIROUTE_API_KEY=omniroute-local
OMNIROUTE_DEFAULT_MODEL=auto/best-chat
```

4) Django geliştirme sunucusunu başlatın:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
& .\.venv\Scripts\Activate.ps1
python manage.py runserver 127.0.0.1:8002
```

5) Tarayıcıda açın: http://127.0.0.1:8002/

## CLI testler

- Uygulama içindeki yardımcı betiği çalıştırıp JSON çıktı almak için:

```powershell
& .\.venv\Scripts\Activate.ps1
python -X utf8 run_task.py --output run_result.json
Get-Content run_result.json -Raw
```

## Görev başlatma (HTTP)

- UI yerine doğrudan bir background task başlatmak için (PowerShell):

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8002/start_task/ -Body @{ task = "print('Selam! Size nasıl yardımcı olabilirim?')" }
```

## Logları görüntüleme

- Her run için `task_runs/` dizininde `<run_id>.log` JSON-lines dosyaları oluşturulur. Örneğin:

```powershell
Get-Content .\task_runs\c027a7e3667645509ff8e8bed3a2cf35.log -Raw
```

## Notlar ve hatırlatmalar
- Windows'ta varsayılan konsol kodlaması cp1252 olduğu için Türkçe karakter sorunlarını önlemek amacıyla `run_task.py` ve alt süreç çağrıları UTF-8 modunda çalıştırılacak şekilde ayarlandı (`-X utf8` ve `PYTHONUTF8=1`).
- OmniRoute yerel olarak çalışmıyorsa `OMNIROUTE_BASE_URL` değerini `.env` içinde güncelleyin ve servis çalıştığından emin olun.

İsterseniz README'ye Docker talimatları veya deploy adımları ekleyebilirim.

## Docker (isteğe bağlı)

Konteyner kullanarak uygulamayı çalıştırmak isterseniz örnek Dockerfile ve `docker-compose.yml` eklendi. Örnek adımlar:

1) `.env.example` dosyasını kopyalayıp kendi değerlerinizi girin:

```powershell
Copy-Item .\.env.example .\.env
# Edit .env (ör. set OMNIROUTE_BASE_URL)
```

2) Docker Compose ile çalıştırma:

```bash
docker compose build
docker compose up
```

Not: Windows Docker Desktop kullanıyorsanız, konteynerin host makinedeki OmniRoute servisine erişebilmesi için `OMNIROUTE_BASE_URL` değerini `http://host.docker.internal:3001/v1` olarak ayarlayabilirsiniz.

## GitHub entegrasyonu

Hızlıca projenizi bir GitHub deposuna göndermek için iki yardımcı betik ekledim:

- `scripts\create_github_repo.ps1` : GitHub CLI (`gh`) yüklüyse onu kullanır, yoksa `GITHUB_TOKEN` ortam değişkeni ile GitHub REST API çağrısı yapar ve yeni bir repo oluşturur.
- `scripts\push_all.ps1` : Yerel repo oluşturur, tüm dosyaları commitler ve verdiğiniz uzak URL'e (`RemoteUrl`) push eder.

PowerShell örneği (örnek adımlar):

```powershell
# 1) Eğer GitHub CLI yoksa önce bir token ayarlayın (Settings -> Developer settings -> Personal access tokens)
$env:GITHUB_TOKEN = 'ghp_...'  # güvenli şekilde ayarlayın

# 2) Oluşturmak istediğiniz repo adıyla scripti çalıştırın (kendi kullanıcı/organizasyon isminizi kullanın)
.\scripts\create_github_repo.ps1 -RepoName "my-emog-repo" -Visibility public

# 3) Script size repo URL döndürdüyse veya GitHub sayfasından URL'yi aldıysanız, push yapmak için:
.\scripts\push_all.ps1 "https://github.com/USERNAME/my-emog-repo.git"
```

Alternatif: Eğer GitHub CLI yüklüyse, şu adımları elle de uygulayabilirsiniz:

```powershell
gh auth login
gh repo create USERNAME/my-emog-repo --public --confirm
git init
git add -A
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/USERNAME/my-emog-repo.git
git push -u origin main
```

Güvenlik notu: `GITHUB_TOKEN` veya kişisel erişim belirteçlerini paylaşılan ortamlarda saklamayın. CI için GitHub Secrets kullanın.

