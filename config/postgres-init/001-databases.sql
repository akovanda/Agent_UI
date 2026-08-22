CREATE DATABASE local_ai_hub;
CREATE DATABASE open_webui;

\connect local_ai_hub
CREATE EXTENSION IF NOT EXISTS vector;

\connect open_webui
CREATE EXTENSION IF NOT EXISTS vector;
