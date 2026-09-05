from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/ui/templates")
templates.env.globals["root_path"] = "/job_scrapper"
