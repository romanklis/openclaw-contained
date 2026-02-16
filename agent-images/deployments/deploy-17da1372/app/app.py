from flask import Flask, render_template_string

app = Flask(__name__)

@app.route('/')
def fibonacci():
    fibs = [0, 1]
    for i in range(2, 20):
        fibs.append(fibs[-1] + fibs[-2])
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Fibonacci Numbers</title>
    </head>
    <body>
        <h1>Fibonacci Numbers</h1>
        <ul>
            {% for num in fibs %}
                <li>{{ num }}</li>
            {% endfor %}
        </ul>
    </body>
    </html>
    """
    return render_template_string(html, fibs=fibs)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
