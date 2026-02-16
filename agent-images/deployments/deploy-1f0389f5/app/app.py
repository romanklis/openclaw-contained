from flask import Flask, render_template_string, request

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Flask Calculator</title>
    <style>
        body { font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background-color: #f0f2f5; }
        .calculator { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 300px; }
        h2 { text-align: center; margin-top: 0; }
        input[type="number"], select { width: 100%; padding: 0.5rem; margin: 0.5rem 0; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        button { width: 100%; padding: 0.75rem; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 1rem; }
        button:hover { background-color: #0056b3; }
        .result { margin-top: 1rem; padding: 1rem; background-color: #e9ecef; border-radius: 4px; text-align: center; font-weight: bold; }
        .error { color: #dc3545; }
    </style>
</head>
<body>
    <div class="calculator">
        <h2>Calculator</h2>
        <form method="POST">
            <input type="number" name="num1" step="any" placeholder="First Number" required value="{{ num1 }}">
            <select name="operation">
                <option value="add" {% if operation == 'add' %}selected{% endif %}>+</option>
                <option value="subtract" {% if operation == 'subtract' %}selected{% endif %}>-</option>
                <option value="multiply" {% if operation == 'multiply' %}selected{% endif %}>×</option>
                <option value="divide" {% if operation == 'divide' %}selected{% endif %}>÷</option>
            </select>
            <input type="number" name="num2" step="any" placeholder="Second Number" required value="{{ num2 }}">
            <button type="submit">Calculate</button>
        </form>

        {% if result is not none %}
            <div class="result">
                Result: {{ result }}
            </div>
        {% endif %}

        {% if error %}
            <div class="result error">
                Error: {{ error }}
            </div>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route('/', methods=['GET', 'POST'])
def calculate():
    result = None
    error = None
    num1 = ''
    num2 = ''
    operation = 'add'

    if request.method == 'POST':
        try:
            num1 = request.form.get('num1')
            num2 = request.form.get('num2')
            operation = request.form.get('operation')
            
            n1 = float(num1)
            n2 = float(num2)

            if operation == 'add':
                result = n1 + n2
            elif operation == 'subtract':
                result = n1 - n2
            elif operation == 'multiply':
                result = n1 * n2
            elif operation == 'divide':
                if n2 == 0:
                    error = "Cannot divide by zero"
                else:
                    result = n1 / n2
            
            # Format result to remove trailing zeros if it's an integer
            if result is not None and result == int(result):
                result = int(result)

        except ValueError:
            error = "Invalid input. Please enter numbers."
        except Exception as e:
            error = str(e)

    return render_template_string(HTML_TEMPLATE, result=result, error=error, num1=num1, num2=num2, operation=operation)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
