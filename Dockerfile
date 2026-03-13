FROM python:3.13-slim

# Устанавливаем системные кодеки для аудио
RUN apt-get update && apt-get install -y ffmpeg

# Создаем рабочую папку
WORKDIR /app

# Копируем библиотеки и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Запускаем бота
CMD ["python", "main.py"]