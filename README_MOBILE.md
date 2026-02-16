# AI Coach → Android & iOS (App Store / Play Store)

This folder wraps your existing **vanilla HTML/JS** UI in a native shell using **Capacitor**, so it can be shipped to:
- Google Play Store (Android)
- Apple App Store (iOS)

⚠️ **Important architectural note**
Your FastAPI Python server **cannot be embedded** into App Store/Play Store builds in a reliable/compliant way. In production you should:
1) **Deploy the FastAPI backend to the cloud** (HTTPS)
2) The mobile app calls that backend via HTTPS

---

## 1) Deploy the backend (FastAPI) to a public HTTPS URL

### Option A — Docker (works for Azure App Service, Render, Fly.io, etc.)
In your existing backend folder (the one that has `server.py` + `requirements.txt`), add:

**Dockerfile**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
ENV PORT=8000
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
```

Then build & run:
```bash
docker build -t aicoach-api .
docker run -p 8000:8000 -e OPENAI_API_KEY=... aicoach-api
```
Deploy that container to your preferred platform and note the final HTTPS URL, e.g.
`https://aicoach-api.yourdomain.com`

### Required environment variables
- `OPENAI_API_KEY` (required)
- `OPENAI_CHAT_MODEL` (optional, default: gpt-4o-mini)

---

## 2) Point the mobile UI to your backend

Edit:
- `www/config.js`

Set:
```js
const DEFAULT_API_BASE = "https://aicoach-api.yourdomain.com";
```

(You can also override at runtime for testing by setting `localStorage.AI_COACH_API_BASE`.)

---

## 3) Build the native apps with Capacitor

### Prereqs
**Windows (Android)**
- Node.js LTS
- Android Studio + Android SDK
- JDK 17

**macOS (iOS)**
- Xcode (required)

### Install dependencies
From this folder:
```bash
npm install
```

### Add native projects
```bash
npx cap add android
npx cap add ios
```

### Sync web assets → native
```bash
npx cap sync
```

### Open in IDEs
```bash
npx cap open android
npx cap open ios
```

---

## 4) Permissions (camera + mic)

### iOS (Xcode)
Ensure these exist in `ios/App/App/Info.plist`:
- `NSCameraUsageDescription`
- `NSMicrophoneUsageDescription`

Example:
- Camera: "AI Coach needs camera access to analyze your stance and food images."
- Microphone: "AI Coach needs microphone access for voice commands."

### Android
Capacitor will add most permissions automatically, but verify in `android/app/src/main/AndroidManifest.xml`:
- `android.permission.CAMERA`
- `android.permission.RECORD_AUDIO`

If your backend is **HTTP** (again: avoid), you must enable cleartext traffic in the manifest or via network security config.

---

## 5) Store readiness checklist

- Use a production HTTPS backend
- Add a privacy policy page/URL (both stores require this for camera/mic + AI)
- Add in-app disclosure that images/audio may be sent to your backend for analysis
- Add app icons + splash screens (Android Studio / Xcode)
- Create release builds:
  - Android: Signed AAB
  - iOS: Archive + upload via Xcode Organizer

---

## What was changed from the original web app

- All `/static/...` links were converted to `./...` so files load inside the app bundle.
- Server routes like `/batting` were converted to `batting.html` for file-based navigation.
- API calls now read the backend URL from `www/config.js`.
