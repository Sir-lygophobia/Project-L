from datetime import datetime

def print_interaction_info(interaction):
    user_info = interaction.user if interaction.user else "Unknown User"
    time_now = datetime.now().strftime("%d %b, %I:%M %p")
    command_name = interaction.data.get("name", "Unknown Command")


    print(f"{user_info} used the command /{command_name} at {time_now}")
