# Age- and Temperature-Adjusted Running Pace Calculator

# 📊 Updated WMA/Yale-inspired age-grade factors for 5K–10K
age_grade_factors = {
    (40, "male"): 0.956,
    (45, "male"): 0.929,
    (50, "male"): 0.898,
    (55, "male"): 0.860,
    (60, "male"): 0.818,
    (65, "male"): 0.790,
    (70, "male"): 0.749,
    (75, "male"): 0.705,
    (80, "male"): 0.660,
    (40, "female"): 0.939,
    (45, "female"): 0.906,
    (50, "female"): 0.867,
    (55, "female"): 0.827,
    (60, "female"): 0.779,
    (65, "female"): 0.735,
    (70, "female"): 0.689,
    (75, "female"): 0.643,
    (80, "female"): 0.596,
}

def linear_interpolate(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

def get_closest_factor(age, gender, distance_km):
    gender = gender.lower()
    keys = sorted([k for k in age_grade_factors if k[1] == gender])
    if not keys:
        return None
    ages = [k[0] for k in keys]
    factors = [age_grade_factors[k] for k in keys]
    if age < ages[0] or age > ages[-1]:
        return None
    for i in range(len(ages) - 1):
        if ages[i] <= age <= ages[i + 1]:
            return linear_interpolate(age, ages[i], ages[i + 1], factors[i], factors[i + 1])
    return factors[-1]

def calculate_temperature_factor(temp_f):
    if temp_f <= 60:
        return 1.0
    elif temp_f <= 70:
        return 0.985
    elif temp_f <= 80:
        return 0.97
    elif temp_f <= 90:
        return 0.92
    else:
        return 0.85

def format_pace(pace, unit):
    minutes = int(pace)
    seconds = int(round((pace - minutes) * 60))
    return f"{minutes}:{seconds:02d} min/{unit}"

def get_valid_int(prompt):
    while True:
        val = input(prompt).strip()
        if val.lower() == 'exit':
            exit()
        try:
            return int(val)
        except ValueError:
            print("Please enter a valid whole number.")

def get_valid_float(prompt):
    while True:
        val = input(prompt).strip()
        if val.lower() == 'exit':
            exit()
        try:
            return float(val)
        except ValueError:
            print("Please enter a valid number.")

def get_valid_gender(prompt):
    while True:
        val = input(prompt).strip().lower()
        if val == 'exit':
            exit()
        if val in ['male', 'female']:
            return val
        print("Please enter 'male' or 'female'.")

def get_valid_unit(prompt):
    while True:
        val = input(prompt).strip().lower()
        if val == 'exit':
            exit()
        if val in ['miles', 'km']:
            return val
        print("Please enter 'miles' or 'km'.")

def get_valid_choice(prompt, options):
    while True:
        val = input(prompt).strip().lower()
        if val == 'exit':
            exit()
        if val in options:
            return val
        print(f"Please enter one of: {', '.join(options)}")

def explain_adjustments(adjustment):
    print("\n📘 What your adjustment means:")
    if adjustment == "age":
        print("⏳ Your age-adjusted result estimates how you would have performed at your peak (age 25–30),")
        print("assuming the same effort and race conditions.")
    elif adjustment == "temp":
        print("🔥 Your temperature-adjusted result shows how you might have performed in ideal weather (around 60°F),")
        print("based on the same effort level.")
    elif adjustment == "both":
        print("🔥⏳ Your age- and temperature-adjusted result estimates how you would have performed at your peak age")
        print("and in ideal weather — giving a picture of your true fitness potential.")

def main():
    print("""
🏃 Welcome to the Age- and Temperature-Adjusted Running Pace Calculator!

This tool adjusts your running performance so you can understand how age and temperature affect your results.

👉 Type 'exit' at any prompt to quit.

You can choose to adjust for:
- ⏳ Age — Estimates how you'd perform at peak age (25–30)
- 🔥 Temperature — Estimates your time under ideal conditions (~60°F)
- 🔥⏳ Both — Gives your best-case performance under ideal age & weather
""")

    age = get_valid_int("Enter your age: ")
    gender = get_valid_gender("Enter your gender (male/female): ")
    unit = get_valid_unit("Would you like to enter distance in miles or km? ")
    distance_input = get_valid_float(f"Enter your race distance in {unit}: ")
    distance_display = distance_input
    distance_km = distance_input * 1.60934 if unit == 'miles' else distance_input

    print("\nEnter your finish time:")
    hours = get_valid_int("  Hours: ")
    minutes = get_valid_int("  Minutes: ")
    seconds = get_valid_int("  Seconds: ")
    raw_time_minutes = hours * 60 + minutes + seconds / 60
    raw_pace = raw_time_minutes / distance_display

    adjustment = get_valid_choice("\nChoose adjustment type ('age', 'temp', or 'both'): ", ['age', 'temp', 'both'])
    explain_adjustments(adjustment)

    temp_adjusted_time = raw_time_minutes
    if adjustment in ['temp', 'both']:
        temperature_f = get_valid_float("Enter the race temperature in °F: ")
        temp_factor = calculate_temperature_factor(temperature_f)
        temp_adjusted_time = raw_time_minutes * temp_factor

    final_time = temp_adjusted_time
    if adjustment in ['age', 'both']:
        factor = get_closest_factor(age, gender, distance_km)
        if factor:
            final_time = temp_adjusted_time * factor
        else:
            print("\n⚠️ No age-adjustment factor found. Age-adjustment skipped.")
            adjustment = 'temp'

    final_pace = final_time / distance_display
    adj_hours = int(final_time // 60)
    adj_minutes = int(final_time % 60)
    adj_seconds = int((final_time * 60) % 60)

    print(f"\n📊 Original pace: {format_pace(raw_pace, unit)}")

    if adjustment == 'temp':
        print(f"🔥 Temp-adjusted time: {int(temp_adjusted_time // 60)}h {int(temp_adjusted_time % 60)}m {int((temp_adjusted_time * 60) % 60)}s")
        print(f"🔥 Temp-adjusted pace: {format_pace(temp_adjusted_time / distance_display, unit)}")
    elif adjustment == 'age':
        print(f"⏳ Age-adjusted time: {adj_hours}h {adj_minutes}m {adj_seconds:.0f}s")
        print(f"⏳ Age-adjusted pace: {format_pace(final_pace, unit)}")
    elif adjustment == 'both':
        print(f"🔥⏳ Temp + Age-adjusted time: {adj_hours}h {adj_minutes}m {adj_seconds:.0f}s")
        print(f"🔥⏳ Temp + Age-adjusted pace: {format_pace(final_pace, unit)}")

    improvement = raw_time_minutes - final_time
    if improvement > 0:
        percent = improvement / raw_time_minutes * 100
        print(f"\n🎉 Your adjusted time is {percent:.1f}% faster than your actual time.")
    else:
        print(f"\nℹ️ No improvement from adjustments (conditions may have been ideal).")

    print("\n🌍 Carbon footprint of running this program: ~0.01 miles driven by a gasoline-powered car.")

if __name__ == "__main__":
    main()