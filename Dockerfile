# Use a lightweight Python base
FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app

# Copy dependency list and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the script into the container
COPY jellylink.py .

# Run the script with the apply flag by default
# We use the config file via a volume mount
CMD ["python", "jellylink.py", "--apply"]

########## EXPLANATION ##########
# - Uses slim-debian base to keep the image small.
# - Installs only necessary dependencies.
# - Expects the config and media to be mounted as volumes.
#################################
