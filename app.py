
from flask import Flask, request, session, redirect, url_for, render_template, flash
import boto3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import uuid
import logging
import os

# ---------------------------------------
# Flask App Initialization
# ---------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'super-secret-key')

# ---------------------------------------
# App Configuration (Inline or via .env)
# ---------------------------------------
AWS_REGION_NAME = os.environ.get('AWS_REGION_NAME', 'us-east-1')
DYNAMODB_USERS_TABLE = os.environ.get('DYNAMODB_USERS_TABLE', 'UsersTable')
DYNAMODB_APPOINTMENTS_TABLE = os.environ.get('DYNAMODB_APPOINTMENTS_TABLE', 'AppointmentsTable')
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
ENABLE_EMAIL = os.environ.get('ENABLE_EMAIL', 'True').lower() == 'true'
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'your@email.com')
SENDER_PASSWORD = os.environ.get('SENDER_PASSWORD', 'your-app-password')

# ---------------------------------------
# AWS Resources
# ---------------------------------------
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION_NAME)
sns = boto3.client('sns', region_name=AWS_REGION_NAME)
user_table = dynamodb.Table(DYNAMODB_USERS_TABLE)
appointment_table = dynamodb.Table(DYNAMODB_APPOINTMENTS_TABLE)

# ---------------------------------------
# Logging
# ---------------------------------------
logging.basicConfig(level=logging.INFO)

# ---------------------------------------
# Helper Functions
# ---------------------------------------
def is_logged_in():
    return 'email' in session

def get_user(email):
    response = user_table.get_item(Key={'email': email})
    return response.get('Item')

def send_email(to_email, subject, body):
    if not ENABLE_EMAIL:
        app.logger.info(f"[Email Skipped] {subject} to {to_email}")
        return
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
        app.logger.info(f"Email sent to {to_email}")
    except Exception as e:
        app.logger.error(f"Email failed: {e}")
        
#----------------------------
# Routers
#----------------------------        
# Home Page        
@app.route('/')
def index():
    if is_logged_in():
        return redirect(url_for('dashboard'))
    return render_template('index.html')
# Register User (Doctor/Patient)
@app.route('/register', methods=['GET', 'POST'])
def register():
    if is_logged_in():
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirmPassword']
        age = request.form['age']
        gender = request.form['gender']
        role = request.form['role']
        specialization = request.form.get('specialization', '')
        
        if password != confirm_password:
            flash('Passwords do not match!', 'danger')
            return render_template('register.html')

        if get_user(email):
            flash('Email already registered!', 'danger')
            return render_template('register.html')

        user_item = {
            'email': email,
            'name': name,
            'password': generate_password_hash(password),
            'age': age,
            'gender': gender,
            'role': role,
            'created_at': datetime.now().isoformat()
        }
        if role == 'doctor':
            user_item['specialization'] = specialization

        user_table.put_item(Item=user_item)

        # Send Welcome Email
        send_email(email, "Welcome to MedTrack!", f"Dear {name},\n\nWelcome to MedTrack. Your account is now active.")

        # Publish to SNS
        if SNS_TOPIC_ARN:
            try:
                sns.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Message=f'New {role} registered: {name} ({email})',
                    Subject='New Registration on MedTrack'
                )
            except Exception as e:
                app.logger.error(f"SNS error: {e}")

        flash('Registration successful. Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')
#login User
@app.route('/login', methods=['GET', 'POST'])
def login():
    if is_logged_in():
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        role = request.form['role']
        
        user = get_user(email)
        if user and check_password_hash(user['password'], password) and user['role'] == role:
            session['email'] = email
            session['role'] = role
            session['name'] = user['name']
            flash('Login successful.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials.', 'danger')
    return render_template('login.html')
# Logout User
@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))
# Dashboard for both Doctors and Patients
@app.route('/dashboard')
def dashboard():
    if not is_logged_in():
        return redirect(url_for('login'))
    role = session['role']
    email = session['email']
    if role == 'doctor':
        appointments = appointment_table.scan(
            FilterExpression="#doctor_email = :email",
            ExpressionAttributeNames={"#doctor_email": "doctor_email"},
            ExpressionAttributeValues={":email": email}
        ).get('Items', [])
        return render_template(
            'doctor_dashboard.html',
            appointments=appointments,
            doctor_name=session.get('name', ''),
            pending_count=sum(1 for a in appointments if a.get('status') == 'pending'),
            completed_count=sum(1 for a in appointments if a.get('status') == 'completed'),
            total_count=len(appointments)
        )
    else:
        appointments = appointment_table.scan(
            FilterExpression="#patient_email = :email",
            ExpressionAttributeNames={"#patient_email": "patient_email"},
            ExpressionAttributeValues={":email": email}
        ).get('Items', [])
        doctors = user_table.scan(
            FilterExpression="#role = :role",
            ExpressionAttributeNames={"#role": "role"},
            ExpressionAttributeValues={":role": 'doctor'}
        ).get('Items', [])
        return render_template(
            'patient_dashboard.html',
            patient_name=session.get('name', ''),
            appointments=appointments,
            doctors=doctors,
            pending_count=sum(1 for a in appointments if a.get('status') == 'pending'),
            completed_count=sum(1 for a in appointments if a.get('status') == 'completed'),
            total_count=len(appointments)
        )
# Book Appointment
@app.route('/book_appointment', methods=['GET', 'POST'])
def book_appointment():
    if not is_logged_in() or session['role'] != 'patient':
        flash('Only patients can book appointments.', 'danger')
        return redirect(url_for('login'))

    # Get list of doctors (for both GET and POST)
    try:
        response = user_table.scan(
            FilterExpression="#role = :role",
            ExpressionAttributeNames={"#role": "role"},
            ExpressionAttributeValues={":role": 'doctor'}
        )
        doctors = response.get('Items', [])
    except Exception as e:
        app.logger.error(f"Failed to fetch doctors: {e}")
        doctors = []

    if request.method == 'POST':
        doctor_email = request.form.get('doctor_email')
        doctor = get_user(doctor_email)
        if not doctor:
            flash('Doctor not found.', 'danger')
            return render_template('book_appointment.html', doctors=doctors, patient_name=session.get('name', ''))

        # Collect patient info and form data
        patient_name = session.get('name', 'Patient')
        patient_email = session['email']
        date = request.form.get('date')
        time = request.form.get('time')
        symptoms = request.form.get('symptoms')

        if not all([doctor_email, date, time, symptoms]):
            flash('All fields are required.', 'danger')
            return render_template('book_appointment.html', doctors=doctors, patient_name=patient_name)

        appointment_id = str(uuid.uuid4())
        appointment_item = {
            'appointment_id': appointment_id,
            'doctor_email': doctor_email,
            'doctor_name': doctor['name'],
            'patient_email': patient_email,
            'patient_name': patient_name,
            'date': date,
            'time': time,
            'symptoms': symptoms,
            'status': 'pending',
            'created_at': datetime.now().isoformat()
        }

        try:
            appointment_table.put_item(Item=appointment_item)

            # Optional SNS Notification
            if SNS_TOPIC_ARN:
                try:
                    sns.publish(
                        TopicArn=SNS_TOPIC_ARN,
                        Message=f"New appointment booked by {patient_name} for Dr. {doctor['name']} on {date} at {time}.",
                        Subject="New Appointment - MedTrack"
                    )
                except Exception as e:
                    app.logger.error(f"SNS error: {e}")

            # Optional Email Notification
            if app.config.get('ENABLE_EMAIL', True):
                send_email(
                    doctor_email,
                    "New Appointment Booked",
                    f"Dear Dr. {doctor['name']},\n\nA new appointment has been booked by {patient_name} for {date} at {time}.\n\nSymptoms: {symptoms}"
                )
                send_email(
                    patient_email,
                    "Appointment Booked",
                    f"Dear {patient_name},\n\nYour appointment with Dr. {doctor['name']} is scheduled for {date} at {time}.\n\nThank you."
                )

            flash('Appointment booked successfully.', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            app.logger.error(f"Failed to book appointment: {e}")
            flash('Failed to book appointment. Please try again.', 'danger')

    return render_template('book_appointment.html', doctors=doctors, patient_name=session.get('name', ''))
# view appointment details
@app.route('/view_appointment/<appointment_id>', methods=['GET', 'POST'])
def view_appointment(appointment_id):
    if not is_logged_in():
        flash('Please log in to continue.', 'danger')
        return redirect(url_for('login'))

    try:
        # Fetch appointment by ID
        response = appointment_table.get_item(Key={'appointment_id': appointment_id})
        appointment = response.get('Item')

        if not appointment:
            flash('Appointment not found.', 'danger')
            return redirect(url_for('dashboard'))

        # Authorization check
        user_email = session['email']
        user_role = session['role']

        if user_role == 'doctor' and appointment['doctor_email'] != user_email:
            flash('You are not authorized to view this appointment.', 'danger')
            return redirect(url_for('dashboard'))
        elif user_role == 'patient' and appointment['patient_email'] != user_email:
            flash('You are not authorized to view this appointment.', 'danger')
            return redirect(url_for('dashboard'))

        # Handle POST (Doctor updating diagnosis)
        if request.method == 'POST' and user_role == 'doctor':
            diagnosis = request.form.get('diagnosis')
            treatment_plan = request.form.get('treatment')
            prescription = request.form.get('prescription')

            appointment_table.update_item(
                Key={'appointment_id': appointment_id},
                UpdateExpression="""
                    SET diagnosis = :d, treatment_plan = :t, prescription = :p,
                        #s = :s, updated_at = :u
                """,
                ExpressionAttributeValues={
                    ':d': diagnosis,
                    ':t': treatment_plan,
                    ':p': prescription,
                    ':s': 'completed',
                    ':u': datetime.now().isoformat()
                },
                ExpressionAttributeNames={
                    '#s': 'status'
                }
            )

            # Email patient if enabled
            if app.config.get('ENABLE_EMAIL', False):
                patient_email = appointment['patient_email']
                patient_name = appointment.get('patient_name', 'Patient')
                doctor_name = appointment.get('doctor_name', 'your doctor')

                message = (
                    f"Dear {patient_name},\n\n"
                    f"Your appointment with Dr. {doctor_name} has been completed.\n\n"
                    f"Diagnosis: {diagnosis}\n"
                    f"Treatment Plan: {treatment_plan}\n"
                    f"Prescription: {prescription}\n\n"
                    f"Please login to your dashboard for full details."
                )

                send_email(patient_email, "Appointment Completed - Diagnosis Available", message)

            flash('Diagnosis submitted successfully.', 'success')
            return redirect(url_for('doctor_dashboard'))

        # Render appropriate template
        if user_role == 'doctor':
            return render_template('view_appointment_doctor.html', appointment=appointment)
        else:
            return render_template('view_appointment_patient.html', appointment=appointment)

    except Exception as e:
        app.logger.error(f"Error in view_appointment: {e}")
        flash('An error occurred. Please try again later.', 'danger')
        return redirect(url_for('dashboard'))
# search appointments
@app.route('/search_appointments', methods=['GET', 'POST'])
def search_appointments():
    if not is_logged_in():
        flash('Please log in to continue.', 'danger')
        return redirect(url_for('login'))

    # Get the search term from either POST or GET
    search_term = request.form.get('search_term', '').strip() if request.method == 'POST' else request.args.get('search_term', '').strip()
    appointments = []

    try:
        if session['role'] == 'doctor':
            if search_term:
                response = appointment_table.scan(
                    FilterExpression="#doctor_email = :email AND contains(#patient_name, :search)",
                    ExpressionAttributeNames={
                        "#doctor_email": "doctor_email",
                        "#patient_name": "patient_name"
                    },
                    ExpressionAttributeValues={
                        ":email": session['email'],
                        ":search": search_term
                    }
                )
            else:
                response = appointment_table.scan(
                    FilterExpression="#doctor_email = :email",
                    ExpressionAttributeNames={"#doctor_email": "doctor_email"},
                    ExpressionAttributeValues={":email": session['email']}
                )
        else:  # patient
            if search_term:
                response = appointment_table.scan(
                    FilterExpression="#patient_email = :email AND (contains(#doctor_name, :search) OR contains(#status, :search))",
                    ExpressionAttributeNames={
                        "#patient_email": "patient_email",
                        "#doctor_name": "doctor_name",
                        "#status": "status"
                    },
                    ExpressionAttributeValues={
                        ":email": session['email'],
                        ":search": search_term
                    }
                )
            else:
                response = appointment_table.scan(
                    FilterExpression="#patient_email = :email",
                    ExpressionAttributeNames={"#patient_email": "patient_email"},
                    ExpressionAttributeValues={":email": session['email']}
                )

        appointments = response.get('Items', [])
    except Exception as e:
        app.logger.error(f"Search failed: {e}")
        flash("Failed to search appointments. Please try again.", "danger")
        return redirect(url_for('dashboard'))

    return render_template('search_results.html', results=appointments, term=search_term)
#profile page
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if not is_logged_in():
        flash('Please log in to continue.', 'danger')
        return redirect(url_for('login'))

    email = session['email']
    try:
        user = user_table.get_item(Key={'email': email}).get('Item', {})
        if not user:
            flash('User not found.', 'danger')
            return redirect(url_for('dashboard'))

        if request.method == 'POST':
            name = request.form.get('name')
            age = request.form.get('age')
            gender = request.form.get('gender')

            update_expression = "SET #name = :name, age = :age, gender = :gender"
            expression_values = {
                ':name': name,
                ':age': age,
                ':gender': gender
            }

            if session['role'] == 'doctor' and request.form.get('specialization'):
                specialization = request.form.get('specialization')
                update_expression += ", specialization = :spec"
                expression_values[':spec'] = specialization

            user_table.update_item(
                Key={'email': email},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
                ExpressionAttributeNames={'#name': 'name'}
            )

            session['name'] = name
            flash('Profile updated successfully.', 'success')
            return redirect(url_for('profile'))

        return render_template('profile.html', user=user)

    except Exception as e:
        app.logger.error(f"Profile error: {e}")
        flash('An error occurred while loading the profile.', 'danger')
        return redirect(url_for('dashboard'))
# submit diagnosis
@app.route('/submit_diagnosis/<appointment_id>', methods=['POST'])
def submit_diagnosis(appointment_id):
    if not is_logged_in() or session.get('role') != 'doctor':
        flash('Only doctors can submit diagnoses.', 'danger')
        return redirect(url_for('login'))
    
    try:
        # Fetch appointment and verify ownership
        appointment = appointment_table.get_item(Key={'appointment_id': appointment_id}).get('Item')
        
        if not appointment or appointment.get('doctor_email') != session['email']:
            flash('Unauthorized access to appointment.', 'danger')
            return redirect(url_for('dashboard'))
        
        # Get form data
        diagnosis = request.form.get('diagnosis', '').strip()
        treatment_plan = request.form.get('treatment_plan', '').strip()
        prescription = request.form.get('prescription', '').strip()

        # Simple validation (optional)
        if not diagnosis:
            flash('Diagnosis cannot be empty.', 'danger')
            return redirect(url_for('view_appointment', appointment_id=appointment_id))

        # Update in DB
        appointment_table.update_item(
            Key={'appointment_id': appointment_id},
            UpdateExpression="SET diagnosis = :d, treatment_plan = :t, prescription = :p, #status = :s, updated_at = :u",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ':d': diagnosis,
                ':t': treatment_plan,
                ':p': prescription,
                ':s': 'completed',
                ':u': datetime.now().isoformat()
            }
        )

        # Notify patient
        if app.config.get('ENABLE_EMAIL', False):
            send_email(
                to=appointment['patient_email'],
                subject="Appointment Completed - Diagnosis Available",
                body=f"""Dear {appointment.get('patient_name', 'Patient')},

Your appointment with Dr. {session.get('name', 'your doctor')} has been marked as completed.

Diagnosis: {diagnosis}

Treatment Plan: {treatment_plan}

Prescription: {prescription}

Please log in to your dashboard for full details.

- MedTrack"""
            )

        flash('Diagnosis submitted successfully.', 'success')
        return redirect(url_for('dashboard'))

    except Exception as e:
        app.logger.error(f"Submit diagnosis error: {e}")
        flash('An error occurred while submitting the diagnosis. Please try again.', 'danger')
        return redirect(url_for('view_appointment', appointment_id=appointment_id))
# ---------------------------------------
# Entrypoint for WSGI (production servers)
# ---------------------------------------
application = app  # For gunicorn, uWSGI, etc.

# For local development
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)

