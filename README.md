# AI-Powered Reddit Job Finder 🤖

A smart Telegram bot that monitors specific Subreddits for freelance job opportunities, filters them based on user preferences, and uses **Google Gemini AI** to analyze the requirements and budget before sending a notification.

## ✨ Features
*   **Interactive Telegram UI:** Users can select their specific field (Frontend, Backend, Full-Stack, Web Design) using inline buttons.
*   **AI Analysis:** Integrates with Google Gemini (Generative AI) to read Reddit job posts, extract core requirements, and estimate budgets in Persian.
*   **Competitor Tracking:** Automatically fetches the number of comments (bids) on a project using Reddit's public JSON endpoints—no Reddit API key required!
*   **CI/CD Pipeline Ready:** Designed to be deployed on Render.com as a Web Service. It includes a lightweight background web server to comply with Render's free tier requirements, allowing auto-deployment on every `git push`.

## 🛠 Tech Stack
*   **Python 3.10+**
*   **pyTelegramBotAPI** (Telebot)
*   **Google Generative AI** (Gemini 1.5 Flash)
*   **Feedparser** & **Requests** (for scraping Reddit RSS)

## 🚀 Deployment (Render.com)
1. Fork or clone this repository.
2. Go to [Render](https://render.com) and create a **New Web Service**.
3. Select your repository.
4. Set the Start Command to: `python bot.py`
5. Add the following **Environment Variables**:
   *   `TELEGRAM_TOKEN`: Your Telegram Bot Token.
   *   `GEMINI_API_KEY`: Your Google Gemini API Key.
6. Deploy! Render will automatically update the bot whenever you push new code to the `main` branch.

## 🧪 Testing
Send `/test` to the bot in Telegram to instantly fetch the latest post from `r/slavelabour` and run it through the AI pipeline.
