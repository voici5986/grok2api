from app import ConfigurationManager, ThreadSafeTokenManager, create_app, initialize_application

config = ConfigurationManager()
token_manager = ThreadSafeTokenManager(config)
initialize_application(config, token_manager)
app = create_app(config, token_manager)