# Project Structure Explanation

Welcome to the project! This structure is based on the Flask framework and uses the "Flask Factory Pattern". For those new to Flask or this design pattern, let's break down what each file and directory is for and how they work together.

## Directory Structure:

### `app/` 
This is the main application directory containing all the core files for our Flask application.

- `__init__.py`: Initializes the Flask app using the Flask factory pattern. This allows for creating multiple instances of the app if needed, e.g., for testing.

- `config.py`: Contains configurations/settings for the Flask application. All environment-specific variables and secrets are typically loaded and accessed here.

- `decorators/`: Contains Python decorators that can be used across the application.
  - `security.py`: Houses security-related decorators, for example, to check the validity of incoming requests.

- `utils/`: Utility functions and helpers to aid different functionalities in the application.
  - `whatsapp_utils.py`: Contains utility functions specifically for handling WhatsApp related operations.

- `views.py`: Represents the main blueprint of the app where the endpoints are defined. In Flask, a blueprint is a way to organize related views and operations. Think of it as a mini-application within the main application with its routes and errors.

## Main Files:

- `run.py`: This is the entry point to run the Flask application. It sets up and runs our Flask app on a server.

- `quickstart.py`: A quickstart guide or tutorial-like code to help new users/developers understand how to start using or contributing to the project.

- `requirements.txt`: Lists all the Python packages and libraries required for this project. They can be installed using `pip`.

## How It Works:

1. **Flask Factory Pattern**: Instead of creating a Flask instance globally, we create it inside a function (`create_app` in `__init__.py`). This function can be configured to different configurations, allowing for better flexibility, especially during testing.

2. **Blueprints**: In larger Flask applications, functionalities can be grouped using blueprints. Here, `views.py` is a blueprint grouping related routes. It's like a subset of the application, handling a specific functionality (in this case, webhook views).

3. **app.config**: Flask uses an object to store its configuration. We can set various properties on `app.config` to control aspects of Flask's behavior. In our `config.py`, we load settings from environment variables and then set them on `app.config`.

4. **Decorators**: These are Python's way of applying a function on top of another, allowing for extensibility and reusability. In the context of Flask, it can be used to apply additional functionality or checks to routes. The `decorators` folder contains such utility functions. For example, `signature_required` in `security.py` ensures that incoming requests are secure and valid.

If you're new to Flask or working on larger Flask projects, understanding this structure can give a solid foundation to build upon and maintain scalable Flask applications.

## Setup and Running the App

### Prerequisites
- Python 3.7 or higher
- WhatsApp Business API access token
- OpenAI API key

### Setup Commands:

```bash
# 1. Create virtual environment
python3 -m venv .venv

# 2. Activate virtual environment
source .venv/bin/activate

# 3. Install requirements
pip install -r requirements.txt

# 4. Create .env file (copy from example.env and fill in your values)
cp example.env .env

# 5. Run the application
python run.py
```

### Alternative activation (if the above doesn't work):
```bash
# For Windows/Git Bash
.venv/Scripts/activate

# For fish shell
source .venv/bin/activate.fish
```

### Environment Variables
Make sure to configure your `.env` file with the following variables:
- `ACCESS_TOKEN`: Your WhatsApp Business API access token
- `PHONE_NUMBER_ID`: Your WhatsApp phone number ID
- `VERIFY_TOKEN`: Webhook verification token
- `OPENAI_API_KEY`: Your OpenAI API key
- `VERSION`: WhatsApp API version (e.g., "v23.0")
- `FLASK_DEBUG`: Set to "True" for development, "False" for production

### Running the App
The app will run on `http://localhost:8000` and your webhook endpoint will be at `http://localhost:8000/webhook`.

**Development Mode (FLASK_DEBUG="True")**:
- Auto-reload enabled - server restarts when code changes
- Debug mode enabled - better error messages
- Interactive debugger available

**Production Mode (FLASK_DEBUG="False")**:
- No auto-reload
- Debug mode disabled for security
- Optimized for performance

### To stop the app:
```bash
# Press Ctrl+C in the terminal where the app is running
```

### To deactivate virtual environment:
```bash
deactivate
```

### Production Deployment
When you deploy the app to a production environment, you might not use run.py directly (especially if you use something like Gunicorn or uWSGI). Instead, you'd just need the application instance, which is created using create_app(). The details of this vary depending on your deployment strategy, but it's a point to keep in mind.